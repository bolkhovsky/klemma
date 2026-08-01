#!/usr/bin/env python3
"""Seed + bootstrap the Bonum meeting-analytics portal demo.

Does both halves of the demo bootstrap:
  Layer A (meeting data): renders ~8 synthetic protocols to markdown, then
    imports them into a project DB at <root>/.klemma/data/klemma.db via the same
    bridge the API uses (so embeddings match query-time).
  Layer B (SaaS auth): creates a demo user + a "Бонум" project in users.db so
    the portal can be logged into.

Usage:
    python scripts/seed_bonum_demo.py \
        --root ~/klemma-bonum-demo \
        --email demo@bonum.ru --password bonum-demo

    # + ~90 days of deterministic history across 4 sites (cross-meeting
    # analytics needs evolving topic arcs, KPI drift, recurring assignees):
    python scripts/seed_bonum_demo.py --root ~/klemma-bonum-demo --history 90

Then run the API with:
    KLEMMA_BONUM_PROJECT_ROOT=~/klemma-bonum-demo \
    KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 \
    KLEMMA_DATA_DIR=~/.klemma \
    uvicorn klemma.api.app:create_app --factory
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running from the repo without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from klemma.meetings import (  # noqa: E402
    BONUM_ROOT_ENV,
    build_state_and_embeddings,
    import_meeting,
    parse_protocol,
)
from klemma.meetings_sites import (  # noqa: E402
    ensure_portal_tables,
    remap_meeting_sites,
    set_access,
    upsert_sites,
)

# ── Synthetic sites registry (webhook `value` shape) ──────────────────────────
# Invented names/leaders — NEVER real client sites (repo is public). The pair
# «ОМС …» vs «ЦФО Затраты» mirrors the structural edge case where an org-prefix
# keyword must disambiguate two units sharing generic tokens.
SITES = [
    {"site_slug": "oms_liteynyi_tseh", "site_name": "ОМС Литейного цеха",
     "site_type": "oms", "leader": "Королёв Андрей",
     "site_keywords": ["омс литейн"], "enabled": True},
    {"site_slug": "oms_sborochnoe_proizvodstvo", "site_name": "ОМС Сборочного производства",
     "site_type": "oms", "leader": "Гусева Марина",
     "site_keywords": ["омс сборочн"], "enabled": True},
    {"site_slug": "oms_otdel_prodazh", "site_name": "ОМС Отдела продаж",
     "site_type": "oms", "leader": "Ветров Павел",
     "site_keywords": ["омс продаж", "отдел продаж"], "enabled": True},
    {"site_slug": "os_snabzheniya", "site_name": "ОС Снабжения",
     "site_type": "procurement", "leader": "Чагин Сергей",
     "site_keywords": ["ос снабжен", "снабжени"], "enabled": True},
    {"site_slug": "tsfo_zatraty", "site_name": "ЦФО Затраты",
     "site_type": "oms", "leader": "Никонова Дарья",
     "site_keywords": ["цфо затрат"], "enabled": True},
]

# ── Demo meeting data ─────────────────────────────────────────────────────────
# Recurring themes are deliberate so insights/search/escalations have signal:
#   • "труба 1420" → m1, m3, m5     • "предоплата Турция" → m1, m2, m7
#   • "1С интеграция" → m4, m8
MEETINGS = [
    {
        "date": "2026-06-24", "type": "ОМС", "site": "Челябинск", "time": "09:00",
        "duration": 42,
        "speakers": ["Илья Болховский", "Анастасия Казимирова", "Михаил Лебедев", "Елена Орлова"],
        "title": "Оперативка: отгрузки и логистика",
        "summary": [
            ("Срыв отгрузок по трубе 1420 и дефицит на складе.", "0:00"),
            ("Контракт с Турцией снова заблокирован из-за несогласованной предоплаты.", "2:01"),
        ],
        "themes": [
            ("Дефицит трубы 1420", [
                ("Анастасия Казимирова", "сообщила о дефиците трубы 1420 на складе, поступление ожидается не раньше следующей недели", "2:01"),
                ("Михаил Лебедев", "предложил искать альтернативного поставщика трубы большого диаметра", "4:31"),
            ]),
            ("Логистика по Турции", [
                ("Елена Орлова", "доложила о задержке контейнера в Турции из-за неоплаты, нужна срочная предоплата", "23:16"),
            ]),
        ],
        "decisions": [
            "Найти альтернативного поставщика трубы 1420 до 27 июня.",
            "Эскалировать вопрос предоплаты по Турции финансовому директору.",
        ],
        "tasks": [
            ("Подготовить список альтернативных поставщиков трубы 1420", "Михаил Лебедев", "27 июн", "12:40"),
            ("Согласовать схему предоплаты по Турции", "Елена Орлова", "просроч. 21 июн", "24:15"),
            ("Свести остатки по складу Челябинск", "Анастасия Казимирова", "25 июн", "31:02"),
        ],
    },
    {
        "date": "2026-06-23", "type": "Продажи", "site": "Москва", "time": "14:20",
        "duration": 35,
        "speakers": ["Анастасия Кованцева", "Елена Орлова", "Илья Болховский"],
        "title": "Воронка Q3 и контракт с Турцией",
        "summary": [
            ("Контракт с Турцией — ключевая сделка квартала, отгрузка под угрозой из-за предоплаты.", "0:00"),
            ("Воронка Q3 в целом по плану, но конверсия в нижней части просела.", "5:10"),
        ],
        "themes": [
            ("Контракт с Турцией", [
                ("Елена Орлова", "подтвердила, что без предоплаты контейнер не выпускают, сделка рискует сорваться", "3:12"),
                ("Анастасия Кованцева", "предложила вынести предоплату по Турции на правление", "8:40"),
            ]),
            ("Воронка Q3", [
                ("Анастасия Кованцева", "отметила просадку конверсии на этапе согласования цены", "14:05"),
            ]),
        ],
        "decisions": [
            "Вынести вопрос предоплаты по Турции на правление.",
        ],
        "tasks": [
            ("Подготовить финмодель по предоплате (Турция)", "Елена Орлова", "просроч. 21 июн", "14:20"),
            ("Обновить прогноз воронки Q3", "Анастасия Кованцева", "29 июн", "22:30"),
        ],
    },
    {
        "date": "2026-06-22", "type": "ОМС", "site": "Тольятти", "time": "09:00",
        "duration": 51,
        "speakers": ["Юрий Казанцев", "Алина Мурадян", "Сергей Мерчалов", "Пётр Зарудинев"],
        "title": "ОМС снабжения: трубы, подшипники, рекламации",
        "summary": [
            ("Дефицит трубы 1420 подтверждён и на площадке Тольятти.", "0:00"),
            ("Поднята новая рекламация по обшивке клиента Элсан.", "28:23"),
        ],
        "themes": [
            ("Дефицит трубы 1420", [
                ("Алина Мурадян", "уточнила даты поступления трубы 1420 с переносом на конец месяца", "4:31"),
            ]),
            ("Подшипники", [
                ("Сергей Мерчалов", "сообщил о низких остатках по подшипникам, нужна проверка склада", "15:27"),
            ]),
            ("Рекламация Элсан", [
                ("Пётр Зарудинев", "поднял вопрос по новой рекламации обшивки клиента Элсан, контактов пока нет", "28:23"),
            ]),
        ],
        "decisions": [
            "Провести инвентаризацию подшипников на складе Тольятти.",
        ],
        "tasks": [
            ("Проверить остатки подшипников и свести дефицит", "Сергей Мерчалов", "26 июн", "16:10"),
            ("Уточнить контакты по рекламации Элсан", "Пётр Зарудинев", "24 июн", "29:00"),
            ("Заполнить данные по дефицитам в 1С", "Алина Мурадян", "просроч. 20 июн", "33:40"),
        ],
    },
    {
        "date": "2026-06-24", "type": "Scrum", "site": "ИТ", "time": "11:30",
        "duration": 18,
        "speakers": ["Дмитрий Соколов", "Павел Климов", "Наталья Власова"],
        "title": "Спринт 34: портал отчётности",
        "summary": [
            ("Спринт по порталу отчётности идёт по плану, релиз дашборда совещаний на следующей неделе.", "0:00"),
            ("Остаётся один блокер — интеграция с 1С.", "3:05"),
        ],
        "themes": [
            ("Интеграция с 1С", [
                ("Дмитрий Соколов", "доложил, что выгрузка из 1С падает на больших объёмах, нужен рефакторинг", "3:05"),
            ]),
            ("Релиз дашборда", [
                ("Павел Климов", "предложил вынести ревью дашборда с заказчиком на конец спринта", "8:22"),
            ]),
        ],
        "decisions": [
            "Закрыть интеграцию с 1С до конца спринта.",
        ],
        "tasks": [
            ("Починить выгрузку из 1С на больших объёмах", "Дмитрий Соколов", "26 июн", "4:10"),
            ("Провести ревью дашборда с заказчиком", "Павел Климов", "28 июн", "9:50"),
        ],
    },
    {
        "date": "2026-06-19", "type": "ОМС", "site": "Челябинск", "time": "09:00",
        "duration": 39,
        "speakers": ["Илья Болховский", "Анастасия Казимирова", "Михаил Лебедев"],
        "title": "Оперативка: производство зерновозов",
        "summary": [
            ("Обсудили план выпуска зерновозов и нехватку кронштейнов.", "0:00"),
            ("Тема трубы 1420 снова всплыла как риск для сборки.", "6:40"),
        ],
        "themes": [
            ("Выпуск зерновозов", [
                ("Михаил Лебедев", "доложил о плане выпуска зерновозов и нехватке кронштейнов на сборке", "2:15"),
            ]),
            ("Дефицит трубы 1420", [
                ("Анастасия Казимирова", "предупредила, что дефицит трубы 1420 тормозит сборку рам", "6:40"),
            ]),
        ],
        "decisions": [
            "Перераспределить кронштейны между площадками.",
        ],
        "tasks": [
            ("Согласовать перераспределение кронштейнов", "Михаил Лебедев", "23 июн", "12:00"),
        ],
    },
    {
        "date": "2026-06-18", "type": "Продажи", "site": "Москва", "time": "15:00",
        "duration": 28,
        "speakers": ["Анастасия Кованцева", "Александр Бабенко"],
        "title": "Лиды против обработки лидов",
        "summary": [
            ("Разбирали гипотезу «лиды плохие» против «лиды обрабатываются плохо».", "0:00"),
        ],
        "themes": [
            ("Качество лидов", [
                ("Александр Бабенко", "показал, что часть лидов теряется на этапе первого звонка, а не из-за качества", "5:30"),
            ]),
        ],
        "decisions": [
            "Прослушать выборку первых звонков отдела продаж.",
        ],
        "tasks": [
            ("Собрать выборку первых звонков для разбора", "Александр Бабенко", "24 июн", "10:15"),
        ],
    },
    {
        "date": "2026-06-17", "type": "ОМС", "site": "Тольятти", "time": "09:00",
        "duration": 46,
        "speakers": ["Юрий Казанцев", "Пётр Зарудинев", "Алина Мурадян"],
        "title": "Снабжение: остатки и предоплата",
        "summary": [
            ("Проблема остатков на складах и переход на предоплату с поставщиками.", "0:00"),
            ("Предоплата по Турции остаётся незакрытой.", "12:30"),
        ],
        "themes": [
            ("Предоплата поставщикам", [
                ("Юрий Казанцев", "обсудил переход на предоплату с Вентал и риски по остаткам", "4:20"),
                ("Пётр Зарудинев", "напомнил, что предоплата по Турции висит без движения вторую неделю", "12:30"),
            ]),
        ],
        "decisions": [
            "Согласовать предоплату с Вентал и проверить возвраты.",
        ],
        "tasks": [
            ("Провести переговоры с Вентал по предоплате", "Юрий Казанцев", "23 июн", "14:00"),
            ("Подготовить справку по незакрытой предоплате Турции", "Пётр Зарудинев", "просроч. 19 июн", "13:10"),
        ],
    },
    {
        "date": "2026-06-17", "type": "Scrum", "site": "ИТ", "time": "11:30",
        "duration": 20,
        "speakers": ["Дмитрий Соколов", "Наталья Власова", "Павел Климов"],
        "title": "Спринт 33: ретроспектива",
        "summary": [
            ("Ретро спринта 33: основной долг — стабилизация интеграции с 1С.", "0:00"),
        ],
        "themes": [
            ("Интеграция с 1С", [
                ("Наталья Власова", "отметила, что интеграция с 1С остаётся главным техдолгом второй спринт подряд", "4:00"),
            ]),
        ],
        "decisions": [
            "Вынести стабилизацию 1С в отдельную задачу спринта 34.",
        ],
        "tasks": [
            ("Завести отдельную задачу на стабилизацию 1С", "Дмитрий Соколов", "20 июн", "6:30"),
        ],
    },
]


# ── Deterministic history generator (--history N) ─────────────────────────────
# Each site runs 2-3 topic arcs with phases (тема появляется → обсуждается →
# эскалация → решение или повтор) so cross-meeting analytics has real signal:
# recurring topics, KPI drift, rotating assignees, ~15% overdue tasks.

HISTORY_SITES = [
    {
        "slug": "oms_liteynyi_tseh",
        "site": "Литейного цеха",  # prefix-less raw site, like prod payloads
        "title": "ОМС Литейного цеха",
        "type": "ОМС",
        "speakers": ["Королёв Андрей", "Мишина Ольга", "Трофимов Денис", "Зайцева Ирина"],
        "arcs": [
            {"title": "Брак литья по корпусам",
             "phases": [
                 "зафиксирован рост брака литья по корпусам, причины уточняются",
                 "брак литья по корпусам держится, идёт разбор с технологами",
                 "брак литья по корпусам блокирует отгрузку, требуется срочное решение",
                 "после ввода входного контроля форм брак литья по корпусам снижается",
             ],
             "escalate_phase": 2, "resolve_phase": 3},
            {"title": "Дебиторская задолженность",
             "kpi": ("дебиторская задолженность", 12.4, 9.8, "млн")},
            {"title": "Ремонт печи №2",
             "phases": [
                 "печь №2 выведена в ремонт, график ремонта согласовывается",
                 "ремонт печи №2 идёт по графику, поставка футеровки подтверждена",
                 "ремонт печи №2 задерживается из-за футеровки, сроки сдвигаются",
                 "печь №2 запущена после ремонта, выход на режим в течение недели",
             ],
             "escalate_phase": 2, "resolve_phase": 3},
        ],
    },
    {
        "slug": "oms_sborochnoe_proizvodstvo",
        "site": "Сборочного производства",
        "title": "ОМС Сборочного производства",
        "type": "ОМС",
        "speakers": ["Гусева Марина", "Лобанов Кирилл", "Фомина Алла"],
        "arcs": [
            {"title": "Дефицит крепежа на сборке",
             "phases": [
                 "выявлен дефицит крепежа на линии финальной сборки",
                 "дефицит крепежа сохраняется, поставщик подтверждает задержку",
                 "дефицит крепежа останавливает финальную сборку, нужна эскалация закупки",
                 "крепёж поступил, финальная сборка восстановлена",
             ],
             "escalate_phase": 2, "resolve_phase": 3},
            {"title": "Выполнение плана сборки",
             "kpi": ("выполнение плана сборки", 82.0, 96.0, "%")},
        ],
    },
    {
        "slug": "oms_otdel_prodazh",
        "site": "Отдела продаж",
        "title": "ОМС Отдела продаж",
        "type": "Продажи",
        "speakers": ["Ветров Павел", "Крылова Софья", "Ежов Артём"],
        "arcs": [
            {"title": "Конверсия воронки",
             "kpi": ("конверсия воронки", 11.5, 14.2, "%")},
            # NB: no "просроч" in the arc title/texts — the parser treats that
            # marker as an overdue flag and would taint every related task.
            {"title": "Неоплаченные счета клиентов",
             "phases": [
                 "растёт объём неоплаченных счетов по ключевым клиентам",
                 "неоплаченные счета обсуждаются повторно, прогресса по оплатам нет",
                 "неоплаченные счета без движения третью неделю, вопрос требует эскалации",
                 "неоплаченные счета без движения, повторная эскалация финансовому директору",
             ],
             "escalate_phase": 2},  # no resolve — recurring problem for analytics
            {"title": "Запуск нового прайс-листа",
             "phases": [
                 "стартовала подготовка нового прайс-листа",
                 "новый прайс-лист согласовывается с производством",
                 "новый прайс-лист утверждён и разослан клиентам",
             ],
             "resolve_phase": 2},
        ],
    },
    {
        "slug": "os_snabzheniya",
        "site": "Снабжения",
        "title": "ОС Снабжения",
        "type": "ОС",
        "speakers": ["Чагин Сергей", "Романова Вера", "Белов Максим", "Осипов Глеб"],
        "arcs": [
            {"title": "Переход на предоплату с поставщиками",
             "phases": [
                 "начат перевод ключевых поставщиков на предоплату",
                 "перевод на предоплату буксует, два поставщика не согласовали условия",
                 "перевод на предоплату заблокирован, эскалация коммерческому директору",
                 "условия предоплаты согласованы со всеми ключевыми поставщиками",
             ],
             "escalate_phase": 2, "resolve_phase": 3},
            {"title": "Остатки металлопроката",
             "kpi": ("остатки металлопроката", 340.0, 210.0, "т")},
        ],
    },
]

_TASK_TEMPLATES = [
    "Подготовить план действий по теме «{arc}»",
    "Свести данные по теме «{arc}» к следующему совещанию",
    "Согласовать ответственных по теме «{arc}»",
    "Обновить статус по теме «{arc}» в отчёте",
]


def _gen_meeting(site_def: dict, day: date, idx: int, progress: float,
                 rng: random.Random) -> dict:
    """One synthetic meeting for a site: 2 arcs, phase-appropriate texts."""
    arcs = site_def["arcs"]
    chosen = [arcs[idx % len(arcs)], arcs[(idx + 1) % len(arcs)]]
    speakers = site_def["speakers"]

    themes = []
    decisions = []
    tasks = []
    summary_bits = []
    for j, arc in enumerate(chosen):
        speaker = speakers[(idx + j) % len(speakers)]
        timecode = f"{2 + j * 9}:{rng.randint(10, 59)}"
        if "kpi" in arc:
            name, v_from, v_to, unit = arc["kpi"]
            value = v_from + (v_to - v_from) * progress + rng.uniform(-0.05, 0.05) * abs(
                v_to - v_from
            )
            text = f"доложил(а), что {name} составляет {value:.1f} {unit}, целевой уровень {v_to:g} {unit}"
            summary_bits.append(f"{name}: {value:.1f} {unit}")
        else:
            phases = arc["phases"]
            phase = min(int(progress * len(phases)), len(phases) - 1)
            text = f"сообщил(а): {phases[phase]}"
            summary_bits.append(phases[phase])
            if phase == arc.get("resolve_phase"):
                decisions.append(f"Зафиксировать закрытие темы «{arc['title']}».")
            if phase == arc.get("escalate_phase") and rng.random() < 0.3:
                # Escalation signal: an overdue task on the hot topic (gated so
                # overall overdue share stays near ~15%, not every meeting).
                who = speakers[(idx + j + 1) % len(speakers)]
                overdue_day = day - timedelta(days=rng.randint(2, 6))
                tasks.append((
                    f"Эскалировать вопрос «{arc['title']}» руководству",
                    who,
                    f"просроч. {overdue_day.strftime('%d.%m')}",
                    f"{15 + j * 5}:{rng.randint(10, 59)}",
                ))
        themes.append((arc["title"], [(speaker, text, timecode)]))

        # 0-2 regular tasks per arc, rotating assignees, ~15% overdue.
        for _ in range(rng.randint(0, 2)):
            who = speakers[rng.randrange(len(speakers))]
            action = rng.choice(_TASK_TEMPLATES).format(arc=arc["title"])
            due = day + timedelta(days=rng.randint(2, 7))
            deadline = due.strftime("%d.%m")
            if rng.random() < 0.1:
                overdue_day = day - timedelta(days=rng.randint(1, 5))
                deadline = f"просроч. {overdue_day.strftime('%d.%m')}"
            tasks.append((action, who, deadline, f"{20 + j * 4}:{rng.randint(10, 59)}"))

    if not decisions and rng.random() < 0.4:
        decisions.append(f"Вынести тему «{chosen[0]['title']}» на следующее совещание.")

    time_str = rng.choice(["08:00", "08:30", "09:00", "09:30"])
    return {
        "date": day.isoformat(),
        "type": site_def["type"],
        "site": site_def["site"],
        "time": time_str,
        "duration": rng.randint(18, 55),
        "speakers": speakers,
        "title": site_def["title"],
        "summary": [(f"{b}." if not b.endswith(".") else b, "0:00") for b in summary_bits[:2]],
        "themes": themes,
        "decisions": decisions,
        "tasks": tasks,
    }


def history_meetings(n_days: int, seed: int = 42) -> list[dict]:
    """~n_days of history across 4 sites, 2-3 meetings/week each. Deterministic
    arc phases, но даты привязаны к date.today() — демо всегда свежее."""
    rng = random.Random(seed)
    today = date.today()
    start = today - timedelta(days=n_days)
    meetings = []
    for site_def in HISTORY_SITES:
        idx = 0
        day = start
        while day <= today:
            # Mon/Wed/Fri cadence with ~20% skips → 2-3 meetings a week.
            if day.weekday() in (0, 2, 4) and rng.random() > 0.2:
                progress = (day - start).days / max(n_days, 1)
                meetings.append(_gen_meeting(site_def, day, idx, progress, rng))
                idx += 1
            day += timedelta(days=1)
    return meetings


def render(m: dict) -> str:
    lines = ["---"]
    lines.append(f"date: {m['date']}")
    lines.append(f"type: {m['type']}")
    lines.append(f"site: {m['site']}")
    lines.append(f"time: \"{m['time']}\"")
    lines.append(f"duration: {m['duration']}")
    lines.append("speakers: [" + ", ".join(m["speakers"]) + "]")
    lines.append("---")
    lines.append("")
    lines.append(f"**{m['title']}**")
    lines.append("")
    lines.append("**Супер краткое содержание**:")
    for text, t in m["summary"]:
        lines.append(f"- {text} [{t}]")
    lines.append("")
    lines.append("**Саммари по темам**:")
    for theme, points in m["themes"]:
        lines.append(f"## {theme}")
        for speaker, text, t in points:
            lines.append(f"- {speaker} {text}. [{t}]")
    lines.append("")
    lines.append("**Принятые решения**:")
    for d in m["decisions"]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("**Задачи:**")
    for action, assignee, deadline, t in m["tasks"]:
        lines.append(
            f"- {action} (**Assignee:** {assignee}, **deadline:** {deadline}) [{t}]"
        )
    lines.append("")
    return "\n".join(lines)


def write_protocols(seed_dir: Path) -> list[Path]:
    seed_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, m in enumerate(MEETINGS, 1):
        stem = f"{m['date']}-{m['type'].lower()}-{i:02d}"
        p = seed_dir / f"{stem}.md"
        p.write_text(render(m), encoding="utf-8")
        paths.append(p)
    return paths


def seed_meetings(state, embeddings, seed_dir: Path) -> dict:
    paths = write_protocols(seed_dir)
    total_frags = total_emb = 0
    for p in paths:
        pm = parse_protocol(p.read_text(encoding="utf-8"))
        r = import_meeting(state, embeddings, pm, p.stem)
        total_frags += r["fragments"]
        total_emb += r["embedded"]
        print(f"  ✓ {r['source_id']}: {r['fragments']} fragments, {r['embedded']} embedded")
    return {"meetings": len(paths), "fragments": total_frags, "embedded": total_emb}


def seed_history(state, embeddings, n_days: int) -> dict:
    """Import the generated history via the same render()/import path as the
    static protocols (identical parsing, embeddings, and slug resolution)."""
    meetings = history_meetings(n_days)
    total_frags = total_emb = 0
    for i, m in enumerate(meetings, 1):
        stem = f"{m['date']}-{m['site']}-{m['time'].replace(':', '-')}"
        pm = parse_protocol(render(m))
        r = import_meeting(state, embeddings, pm, stem)
        total_frags += r["fragments"]
        total_emb += r["embedded"]
        if i % 25 == 0 or i == len(meetings):
            print(f"  … {i}/{len(meetings)} history meetings imported")
    return {"meetings": len(meetings), "fragments": total_frags, "embedded": total_emb}


def print_site_distribution(state) -> None:
    """Visible remap report — silent mis-mapping must be impossible."""
    result = remap_meeting_sites(state)
    names = {s["site_slug"]: s["site_name"] for s in SITES}
    print(f"→ Site mapping: {result['mapped']} mapped, {result['unmapped']} unmapped")
    for slug, count in sorted(result["distribution"].items(), key=lambda kv: (-kv[1], kv[0])):
        label = names.get(slug, slug) if slug else "(не сопоставлено)"
        print(f"    {label:<36} {count}")


def seed_saas(data_dir: Path, email: str, password: str) -> tuple[str, str]:
    """Create demo user + 'Бонум' project in users.db. Idempotent."""
    from klemma.api.auth.password import hash_password
    from klemma.stores.user_store import LocalUserStore

    store = LocalUserStore(data_dir / "users.db")
    user = store.get_user_by_email(email)
    if user is None:
        user = store.create_user(
            email=email, password_hash=hash_password(password), name="Бонум Демо"
        )
        store.grant_tokens(user.user_id, 1_000_000)
    # Reuse an existing "Бонум" project if present
    try:
        projects = store.get_projects(user.user_id)
    except Exception:
        projects = []
    proj = next((p for p in projects if p.get("name") == "Бонум"), None)
    if proj is None:
        proj = store.create_project(user.user_id, "Бонум", "dissertation")
    return user.user_id, proj["project_id"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the Bonum meeting portal demo")
    ap.add_argument("--root", default="~/klemma-bonum-demo",
                    help="Layer A meeting-project root (.klemma/data lives here)")
    ap.add_argument("--data-dir", default=None,
                    help="SaaS data dir for users.db (default: $KLEMMA_DATA_DIR or ~/.klemma)")
    ap.add_argument("--email", default="demo@bonum.ru")
    ap.add_argument("--password", default="bonum-demo")
    ap.add_argument("--no-saas", action="store_true", help="Skip SaaS user/project creation")
    ap.add_argument("--history", type=int, default=0, metavar="N",
                    help="Also generate ~N days of deterministic meeting history "
                         "across 4 sites (0 = static demo protocols only)")
    args = ap.parse_args()

    import os

    root = Path(args.root).expanduser()
    seed_dir = Path(__file__).resolve().parent / "seed_bonum_meetings"

    state, embeddings = build_state_and_embeddings(root)
    if embeddings is None:
        print("⚠ No embedding backend resolved — importing without vectors "
              "(semantic search / ask will be empty).")

    # Sites first so import_meeting resolves site_slug at write time.
    print("→ Registering synthetic portal sites")
    ensure_portal_tables(state)
    upsert_sites(state, SITES)

    print(f"→ Importing meetings into {root}/.klemma/data/klemma.db")
    stats = seed_meetings(state, embeddings, seed_dir)
    print(f"  Imported {stats['meetings']} meetings, {stats['fragments']} fragments, "
          f"{stats['embedded']} embedded.\n")

    if args.history > 0:
        print(f"→ Generating ~{args.history} days of history (deterministic, seed=42)")
        h = seed_history(state, embeddings, args.history)
        print(f"  Imported {h['meetings']} history meetings, {h['fragments']} fragments, "
              f"{h['embedded']} embedded.\n")

    print_site_distribution(state)
    print()

    project_id = "<run with SaaS>"
    if not args.no_saas:
        data_dir = Path(
            args.data_dir or os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
        ).expanduser()
        print(f"→ Creating SaaS user + project in {data_dir}/users.db")
        try:
            user_id, project_id = seed_saas(data_dir, args.email, args.password)
            # Explicit access row (directors also work with NO row — this is
            # the example of how leader/director rows are written).
            set_access(state, user_id, "director", [])
            print(f"  user_id={user_id}  project_id={project_id}  access=director\n")
        except Exception as e:  # pragma: no cover - bootstrap convenience
            print(f"  ⚠ SaaS bootstrap skipped: {e}\n")

    print("─" * 64)
    print("Run the portal backend with:\n")
    print(f"  export {BONUM_ROOT_ENV}={root}")
    print("  export KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1")
    print("  uvicorn klemma.api.app:create_app --factory\n")
    print(f"Login: {args.email} / {args.password}")
    print(f"Portal project_id: {project_id}")
    print(f"Open: /{project_id}/portal/meetings")


if __name__ == "__main__":
    main()
