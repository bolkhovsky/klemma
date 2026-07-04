"""Tests for the portal sites registry, resolver, and access control.

Site fixtures are SYNTHETIC but mirror the structural cases the resolver must
handle in production: site name appearing without its org-prefix word, keyword
multi-word prefix matching, ЦФО-vs-ОМС disambiguation (shared trailing token,
different prefix), latin/cyrillic keyword variants, and unmatched → ''.
"""

from datetime import date, timedelta

from klemma.meetings import ParsedMeeting, ParsedTask, import_meeting, list_meetings
from klemma.meetings_sites import (
    allowed_slugs,
    ensure_portal_tables,
    get_access,
    get_sites,
    parse_sites_webhook,
    remap_meeting_sites,
    resolve_site_slug,
    set_access,
    site_display_names,
    upsert_sites,
)
from klemma.state import StateManager

SITES = [
    {"slug": "oms_remontnyi_uchastok", "name": "ОМС Ремонтного участка",
     "keywords": ["омс ремонтн"], "enabled": True},
    {"slug": "oms_severnyi_filial", "name": "ОМС Северный филиал",
     "keywords": ["омс северн"], "enabled": True},
    {"slug": "oms_vympel", "name": "ОМС ВЫМПЕЛ",
     "keywords": ["омс вымпел"], "enabled": True},
    {"slug": "tsfo_vympel", "name": "ЦФО ВЫМПЕЛ",
     "keywords": ["цфо вымпел"], "enabled": True},
    {"slug": "komanda_77_debitorka", "name": "Дебиторская задолженность",
     "keywords": ["дебиторская задолженность команда 77"], "enabled": True},
    {"slug": "oms_zagotovka", "name": "ОМС Заготовительный участок",
     "keywords": ["омс заготовительн", "oms заготовительн"], "enabled": True},
    {"slug": "closed_site", "name": "ОМС Закрытый объект",
     "keywords": ["омс закрыт"], "enabled": False},
]


# ── Resolver ──────────────────────────────────────────────────────────────────


def test_resolver_full_name_in_title():
    # Site string carries the name without the org prefix; title has it in full.
    assert resolve_site_slug(
        "Ремонтного участка", "ОМС Ремонтного участка", SITES
    ) == "oms_remontnyi_uchastok"


def test_resolver_daily_prefix_title():
    assert resolve_site_slug("ВЫМПЕЛ", "Ежедневный ОМС ВЫМПЕЛ", SITES) == "oms_vympel"


def test_resolver_tsfo_vs_oms_disambiguation():
    # "ЦФО ВЫМПЕЛ" must NOT resolve to the ОМС unit: the keyword "омс вымпел"
    # requires both words, and "омс" is absent from the text.
    assert resolve_site_slug("", "ЦФО ВЫМПЕЛ", SITES) == "tsfo_vympel"


def test_resolver_keyword_prefix_survives_inflection():
    # Genitive inflection breaks the full-name substring; the keyword prefix
    # ("северн" ⊂ "северного") still matches.
    assert resolve_site_slug(
        "Северный филиал", "Отчет ОМС Директора Северного филиала", SITES
    ) == "oms_severnyi_filial"


def test_resolver_multiword_keyword():
    assert resolve_site_slug(
        "", "Стендап Команда 77 Дебиторская Задолженность", SITES
    ) == "komanda_77_debitorka"


def test_resolver_latin_keyword_variant():
    assert resolve_site_slug(
        "", "Отчет OMS Заготовительный участок", SITES
    ) == "oms_zagotovka"


def test_resolver_stem_match_survives_broken_title():
    # Observed in production: the transcriber emits a junk title
    # ("# ПРОТОКОЛ СОВЕЩАНИЯ") so only the folder-derived site string is
    # usable — and the registry name is genitive ("Северного филиала") while
    # the folder is nominative ("Северный филиал"). Token-stem comparison
    # ("северн...") must still map it.
    # (Adjective must be ≥8 chars — the ≥5-char stem floor deliberately keeps
    # short words exact, so "южного"-style 6-char forms stay conservative.)
    sites = SITES + [
        {"slug": "oms_zarechnyi_filial", "name": "Отчет ОМС Директора Заречного филиала",
         "keywords": ["омс заречн"], "enabled": True},
    ]
    assert resolve_site_slug(
        "Заречный филиал", "# ПРОТОКОЛ СОВЕЩАНИЯ", sites
    ) == "oms_zarechnyi_filial"


def test_resolver_stem_no_false_positive_on_short_tokens():
    # Short tokens (≤5 chars) are not loosened: "аксай"-style fragments must
    # not match a longer stem, and an unrelated short name stays unmatched.
    assert resolve_site_slug("", "Стендап ЦФО Юг", SITES) == ""


def test_resolver_unmatched_returns_empty():
    assert resolve_site_slug("Марс", "Планёрка колонистов", SITES) == ""


def test_resolver_disabled_sites_do_not_participate():
    assert resolve_site_slug("", "ОМС Закрытый объект", SITES) == ""


def test_resolver_robust_to_empty_inputs():
    assert resolve_site_slug("", "", SITES) == ""
    assert resolve_site_slug("ВЫМПЕЛ", "ОМС ВЫМПЕЛ", []) == ""
    assert resolve_site_slug("", "", []) == ""


def test_resolver_accepts_webhook_key_form():
    # Raw webhook `value` dicts (site_slug/site_name/site_keywords) work too —
    # the sync script resolves against them in --dry-run before any upsert.
    webhook_sites = [{"site_slug": "oms_x", "site_name": "ОМС Икс",
                      "site_keywords": ["омс икс"], "enabled": True}]
    assert resolve_site_slug("Икс", "ОМС Икс", webhook_sites) == "oms_x"


# ── Webhook payload parsing ───────────────────────────────────────────────────

WRAPPED = {
    "result": [
        {"collection_name": "sites",
         "value": {"site_slug": "oms_a", "site_name": "ОМС А", "site_type": "oms",
                   "leader": "Иванов", "site_keywords": ["омс а"], "enabled": True,
                   "bitrix_chat_id": "42"}},
        {"collection_name": "other",
         "value": {"site_slug": "nope", "site_name": "Nope"}},
        {"value": {"site_slug": "oms_b", "site_name": "ОМС Б"}},  # no collection_name
    ]
}


def test_parse_webhook_wrapped():
    items = parse_sites_webhook(WRAPPED)
    assert [i["site_slug"] for i in items] == ["oms_a", "oms_b"]


def test_parse_webhook_bare_list():
    items = parse_sites_webhook(WRAPPED["result"])
    assert [i["site_slug"] for i in items] == ["oms_a", "oms_b"]
    # bare value dicts without a wrapper also pass
    assert parse_sites_webhook([{"site_slug": "oms_c", "site_name": "ОМС В"}]) != []


def test_parse_webhook_garbage():
    assert parse_sites_webhook(None) == []
    assert parse_sites_webhook("nonsense") == []
    assert parse_sites_webhook({"result": "oops"}) == []
    assert parse_sites_webhook([1, "two", None]) == []
    assert parse_sites_webhook({"result": [{"collection_name": "sites", "value": 7}]}) == []


# ── Registry storage ──────────────────────────────────────────────────────────


def _state(tmp_path) -> StateManager:
    return StateManager(str(tmp_path / "data" / "klemma.db"))


def test_upsert_idempotent_and_update(tmp_path):
    state = _state(tmp_path)
    items = parse_sites_webhook(WRAPPED)
    assert upsert_sites(state, items) == 2
    assert upsert_sites(state, items) == 2  # replace, not duplicate
    sites = get_sites(state)
    assert len(sites) == 2
    a = next(s for s in sites if s["slug"] == "oms_a")
    assert a["name"] == "ОМС А"
    assert a["keywords"] == ["омс а"]
    assert a["leader"] == "Иванов"
    # bitrix fields are dropped from storage
    assert "bitrix_chat_id" not in a

    # Update on re-sync: renamed + disabled
    upsert_sites(state, [{"site_slug": "oms_a", "site_name": "ОМС А (новая)",
                          "enabled": False}])
    assert len(get_sites(state)) == 1  # enabled_only hides the disabled one
    all_sites = get_sites(state, enabled_only=False)
    assert next(s for s in all_sites if s["slug"] == "oms_a")["name"] == "ОМС А (новая)"
    assert site_display_names(state)["oms_a"] == "ОМС А (новая)"


# ── Access semantics ──────────────────────────────────────────────────────────


def test_access_no_row_is_director(tmp_path):
    state = _state(tmp_path)
    assert get_access(state, "ghost") == {"role": "director", "site_slugs": []}
    assert allowed_slugs(state, "ghost") is None


def test_access_roundtrip(tmp_path):
    state = _state(tmp_path)
    set_access(state, "u1", "leader", ["oms_a", "oms_b"])
    assert get_access(state, "u1") == {"role": "leader", "site_slugs": ["oms_a", "oms_b"]}
    assert allowed_slugs(state, "u1") == {"oms_a", "oms_b"}
    # Upsert replaces
    set_access(state, "u1", "director", [])
    assert allowed_slugs(state, "u1") is None


def test_access_rejects_bad_role(tmp_path):
    state = _state(tmp_path)
    try:
        set_access(state, "u1", "admin", [])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── Remap + write-time resolution ─────────────────────────────────────────────


def _mk_meeting(state, *, date_str, site, title, overdue=False):
    pm = ParsedMeeting(
        title=title,
        summary="Обсудили статус.",
        tasks=[ParsedTask(action="Сделать отчёт", assignee="Иванов",
                          deadline="просроч. вчера" if overdue else "завтра",
                          overdue=overdue)],
        meta={"date": date_str, "site": site, "type": "ОМС", "time": "09:00",
              "speakers": []},
    )
    return import_meeting(state, None, pm, f"{date_str}-{title}")


def test_remap_distribution(tmp_path):
    state = _state(tmp_path)
    # Meetings imported BEFORE the registry exists → site_slug ''
    _mk_meeting(state, date_str="2026-06-01", site="Ремонтного участка",
                title="ОМС Ремонтного участка")
    _mk_meeting(state, date_str="2026-06-02", site="ВЫМПЕЛ",
                title="Ежедневный ОМС ВЫМПЕЛ")
    _mk_meeting(state, date_str="2026-06-03", site="Марс", title="Планёрка")
    upsert_sites(state, [
        {"site_slug": s["slug"], "site_name": s["name"],
         "site_keywords": s["keywords"], "enabled": s["enabled"]}
        for s in SITES
    ])
    result = remap_meeting_sites(state)
    assert result["mapped"] == 2
    assert result["unmapped"] == 1
    assert result["distribution"] == {
        "oms_remontnyi_uchastok": 1, "oms_vympel": 1, "": 1,
    }


def test_import_resolves_slug_at_write_time(tmp_path):
    state = _state(tmp_path)
    ensure_portal_tables(state)
    upsert_sites(state, [{"site_slug": "oms_vympel", "site_name": "ОМС ВЫМПЕЛ",
                          "site_keywords": ["омс вымпел"], "enabled": True}])
    _mk_meeting(state, date_str="2026-06-02", site="ВЫМПЕЛ",
                title="Ежедневный ОМС ВЫМПЕЛ")
    payload = list_meetings(state, sites={"oms_vympel"})
    assert payload["stats"]["meetings"] == 1
    # Display name from the registry replaces the raw site string
    assert payload["meetings"][0]["site"] == "ОМС ВЫМПЕЛ"


def test_list_meetings_site_and_days_filters(tmp_path):
    state = _state(tmp_path)
    upsert_sites(state, [{"site_slug": "oms_vympel", "site_name": "ОМС ВЫМПЕЛ",
                          "site_keywords": ["омс вымпел"], "enabled": True}])
    today = date.today()
    _mk_meeting(state, date_str=(today - timedelta(days=2)).isoformat(),
                site="ВЫМПЕЛ", title="ОМС ВЫМПЕЛ")
    _mk_meeting(state, date_str=(today - timedelta(days=40)).isoformat(),
                site="ВЫМПЕЛ", title="Ежедневный ОМС ВЫМПЕЛ")
    _mk_meeting(state, date_str=(today - timedelta(days=2)).isoformat(),
                site="Марс", title="Планёрка")  # unresolved slug ''

    assert list_meetings(state)["stats"]["meetings"] == 3
    # Unresolved passes only without a sites filter
    assert list_meetings(state, sites={"oms_vympel"})["stats"]["meetings"] == 2
    # Days window cuts the 40-day-old meeting
    assert list_meetings(state, sites={"oms_vympel"}, days=30)["stats"]["meetings"] == 1
    assert list_meetings(state, days=30)["stats"]["meetings"] == 2
