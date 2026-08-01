"""Tests for the meeting-report importer parser and record mapping."""

import json

from klemma.meetings import build_records, parse_protocol

SAMPLE = """\
---
date: 2026-06-24
type: ОМС
site: Челябинск
time: "09:00"
duration: 42
speakers: [Илья Болховский, Анастасия Казимирова, Михаил Лебедев, Елена Орлова]
---

**Оперативка: отгрузки и логистика**

**Супер краткое содержание**:
- Срыв отгрузок по трубе 1420 и дефицит на складе. [0:00](#00:00:00)
- Контракт с Турцией заблокирован из-за предоплаты. [2:01](#00:02:01)

**Саммари по темам**:
## Срыв отгрузок по трубе 1420
- Анастасия Казимирова сообщила о дефиците трубы 1420 на складе. [2:01](#00:02:01)
- Михаил Лебедев предложил искать альтернативного поставщика. [4:31](#00:04:31)
## Контракт с Турцией
- Елена Орлова доложила о блокировке контейнера из-за предоплаты. [23:16](#00:23:16)

**Принятые решения**:
- Найти альтернативного поставщика труб до 27 июня.
- Эскалировать предоплату по Турции финансовому директору.

**Задачи:**
- Подготовить список альтернативных поставщиков труб (**Assignee:** Михаил Лебедев, **deadline:** 27 июн) [12:40]
- Согласовать схему предоплаты по Турции (**Assignee:** Елена Орлова, **deadline:** просроч. 21 июн) [24:15]
- Свести остатки по складу [0:31]
"""


def test_parse_protocol_structure():
    pm = parse_protocol(SAMPLE)
    assert pm.title == "Оперативка: отгрузки и логистика"
    assert pm.meta["type"] == "ОМС"
    assert pm.meta["speakers"][0] == "Илья Болховский"
    # Summary joined from the two bullets
    assert "Срыв отгрузок" in pm.summary
    assert "Турцией" in pm.summary
    # Two themes captured
    assert pm.themes == ["Срыв отгрузок по трубе 1420", "Контракт с Турцией"]
    # Three theme points
    assert len(pm.points) == 3
    assert pm.points[0].theme == "Срыв отгрузок по трубе 1420"
    assert pm.points[0].speaker == "Анастасия Казимирова"
    assert pm.points[0].timecode == "2:01"
    # Decisions
    assert len(pm.decisions) == 2
    # Tasks
    assert len(pm.tasks) == 3


def test_parse_tasks_fields():
    pm = parse_protocol(SAMPLE)
    t0 = pm.tasks[0]
    assert t0.assignee == "Михаил Лебедев"
    assert t0.deadline == "27 июн"
    assert t0.timecode == "12:40"
    assert t0.overdue is False
    assert "альтернативных поставщиков" in t0.action
    assert "**Assignee" not in t0.action  # annotation stripped from action

    t1 = pm.tasks[1]
    assert t1.overdue is True  # "просроч." marker
    # Task with no annotation still parses an action
    t2 = pm.tasks[2]
    assert t2.action.startswith("Свести остатки")
    assert t2.assignee == ""


def test_build_records_mapping():
    pm = parse_protocol(SAMPLE)
    source_id, meta, frags = build_records(pm, "operativka")
    assert source_id.startswith("mtg-2026-06-24")
    assert meta["type"] == "ОМС"
    assert meta["title"] == "Оперативка: отгрузки и логистика"

    by_type: dict[str, int] = {}
    for f in frags:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    assert by_type["summary"] == 3
    assert by_type["decision"] == 2
    assert by_type["task"] == 3

    # Task fragment carries assignee/deadline/timecode in usage_hint JSON
    task_frag = next(f for f in frags if f["type"] == "task")
    hint = json.loads(task_frag["usage_hint"])
    assert "assignee" in hint and "deadline" in hint and "timecode" in hint

    # Overdue task is tagged as escalation
    overdue = [f for f in frags if f["type"] == "task" and f["citation_intent"] == "escalation"]
    assert len(overdue) == 1

    # Summary fragment carries speaker + timecode
    summ = next(f for f in frags if f["type"] == "summary")
    shint = json.loads(summ["usage_hint"])
    assert "speaker" in shint and "timecode" in shint


def test_no_frontmatter_is_safe():
    pm = parse_protocol("**Планёрка**\n\n**Задачи:**\n- Сделать отчёт (**Assignee:** Иван)\n")
    assert pm.title == "Планёрка"
    assert len(pm.tasks) == 1
    assert pm.tasks[0].assignee == "Иван"


NODUL_PAYLOAD = {
    "meeting_id": "79847382-a95a-4fd3-bf37-b8e02e96dd48",
    "date": "2026-06-24",
    "type": "ОМС",
    "site": "Челябинск",
    "time": "09:00",
    "duration": 31,
    "speakers": ["Анна Иванова", "Пётр Сидоров"],
    "protocol_md": (
        "**Оперативка по трубе**\n\n"
        "**Супер краткое содержание**:\n"
        "- Дефицит трубы 1420 на складе. [0:00]\n\n"
        "**Саммари по темам**:\n"
        "## Дефицит трубы 1420\n"
        "- Анна Иванова сообщила о дефиците трубы 1420. [2:01]\n\n"
        "**Принятые решения**:\n"
        "- Найти альтернативного поставщика.\n"
    ),
    "tasks": [
        {"assignee": "Пётр Сидоров", "action": "Найти поставщика трубы 1420",
         "deadline": "просроч. 21 июн", "timecode": "12:40"},
        {"assignee": "Анна Иванова", "action": "Свести остатки", "deadline": "26 июн", "timecode": "8:00"},
    ],
}


def test_parse_nodul_payload():
    from klemma.meetings import parse_nodul_payload

    mid, pm = parse_nodul_payload(NODUL_PAYLOAD)
    assert mid == "79847382-a95a-4fd3-bf37-b8e02e96dd48"
    assert pm.meta["type"] == "ОМС"
    assert pm.meta["speakers"] == ["Анна Иванова", "Пётр Сидоров"]
    assert pm.title == "Оперативка по трубе"
    # summary + theme parsed from protocol_md
    assert "Дефицит трубы 1420" in pm.summary
    assert pm.themes == ["Дефицит трубы 1420"]
    assert pm.points[0].speaker == "Анна Иванова"
    # tasks come from the structured list (2), overdue detected
    assert len(pm.tasks) == 2
    assert pm.tasks[0].assignee == "Пётр Сидоров"
    assert pm.tasks[0].overdue is True
    assert pm.tasks[1].overdue is False


def test_ingest_meeting_idempotent_replace(tmp_path):
    from klemma.meetings import ingest_meeting
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    r1 = ingest_meeting(state, None, NODUL_PAYLOAD)
    src = state.get_source(r1["source_id"])
    assert src["source_type"] == "meeting"
    frags1 = state.get_fragments(source_id=r1["source_id"], limit=1000)
    assert len(frags1) == r1["fragments"]
    n_tasks_1 = sum(1 for f in frags1 if f["fragment_type"] == "task")
    assert n_tasks_1 == 2

    # Re-ingest the SAME meeting with one fewer task → replace, not append
    payload2 = dict(NODUL_PAYLOAD, tasks=NODUL_PAYLOAD["tasks"][:1])
    r2 = ingest_meeting(state, None, payload2)
    assert r2["source_id"] == r1["source_id"]
    frags2 = state.get_fragments(source_id=r2["source_id"], limit=1000)
    n_tasks_2 = sum(1 for f in frags2 if f["fragment_type"] == "task")
    assert n_tasks_2 == 1  # replaced, not 3


# Synthetic protocol mirroring the real Bitrix-disk dialect: docx built from
# Word paragraph styles → pandoc docx→gfm renders section labels as plain text
# (no #/##), metadata/risks/tasks as pipe tables, bullets wrap across physical
# lines, and punctuation is backslash-escaped. Fictitious company/site/names.
PANDOC_SAMPLE = """\
ПРОТОКОЛ СОВЕЩАНИЯ

Отчет ОМС Северного филиала

| **Дата:** | **вторник, 10 марта 2026 г.** |
|----|----|
| Время: | 08:00 |
| Длительность: | 22 мин |
| Платформа: | Zoom |
| Расшифровка встречи (MyMeet): | https://app.mymeet.ai/ru/meetings/abc123 |

Краткая сводка

| **Статус производства:** | **Требует внимания** |
|----|----|
| Критические проблемы: | Дефицит крепежа на складе |
| Задач назначено: | 2 |
| Требуют эскалации: | 1 |
| Рекомендация: | Ускорить закупку крепежа |

Участники

| **№** | **ФИО** | **Роль** |
|-------|---------|----------|
| 1 | Сергеева Анна | Директор филиала |
| 2 | SPEAKER_00 \\[не идентифицирован\\] | Участник |

Повестка

\\_Повестка не сформирована\\_

Производство по направлениям

| **Направление** | **Статус** | **Отставание** | **Причина** |
|-----------------|------------|-----------------|-------------|
| Сборка | В графике | — | — |

Резюме

Дефицит крепежа на складе

• Обнаружена нехватка крепежа для финальной сборки, поставщик подтвердил
задержку до конца недели 4:12

Контроль качества

• Технологический контроль замечаний не выявил 9:40

Риски и эскалации

| **Риск** | **Влияние** | **Статус** | **Эскалация** |
|----------|-------------|------------|---------------|
| Дефицит крепежа — поставка сдвинута | Останавливает финальную сборку | Критично | Да |
| Задержка окраски | Косметический дефект | В работе | Нет |

Задачи

| **№** | **Задача** | **Ответственный** | **Срок** | **Приоритет** |
|-------|------------|--------------------|----------|---------------|
| 1 | Согласовать срочную закупку крепежа | Сергеева Анна | 12.03 чт | |
| —     | —          | —                 | —        | —             |

Задачи по сотрудникам

**Сергеева Анна:**

1\\. Согласовать срочную закупку крепежа (12.03 чт, )

Метрики совещания

| **Параметр** | **Значение** |
|----|----|
| Длительность | 22 мин |

*Протокол сформирован автоматически*

*Система: BONUM AI Protocol v0.2*

*ID записи: abc123*
"""


def test_parse_pandoc_protocol_dispatch():
    pm = parse_protocol(PANDOC_SAMPLE)
    assert pm.title == "Отчет ОМС Северного филиала"


def test_parse_pandoc_protocol_summary_and_participants():
    pm = parse_protocol(PANDOC_SAMPLE)
    assert "Требует внимания" in pm.summary
    assert "Ускорить закупку крепежа" in pm.summary
    # Unidentified-speaker bracket annotation unescaped, placeholder rows excluded
    assert pm.meta["speakers"] == ["Сергеева Анна", "SPEAKER_00 [не идентифицирован]"]


def test_parse_pandoc_protocol_resume_themes_and_wrapped_bullet():
    pm = parse_protocol(PANDOC_SAMPLE)
    assert pm.themes == ["Дефицит крепежа на складе", "Контроль качества"]
    assert len(pm.points) == 2
    # Wrapped across two physical lines, joined with a space; trailing timecode stripped
    assert "поставщик подтвердил задержку" in pm.points[0].text
    assert pm.points[0].timecode == "4:12"
    assert pm.points[0].theme == "Дефицит крепежа на складе"


def test_parse_pandoc_protocol_risks_split_escalation_vs_decision():
    pm = parse_protocol(PANDOC_SAMPLE)
    # Escalation risk becomes a pseudo-task (status="escalation"); non-escalation → decision
    escalations = [t for t in pm.tasks if t.status == "escalation"]
    assert len(escalations) == 1
    assert "Дефицит крепежа" in escalations[0].action
    assert len(pm.decisions) == 1
    assert "Задержка окраски" in pm.decisions[0]


def test_parse_pandoc_protocol_tasks_table():
    pm = parse_protocol(PANDOC_SAMPLE)
    real_tasks = [t for t in pm.tasks if t.status != "escalation"]
    assert len(real_tasks) == 1
    task = real_tasks[0]
    assert task.action == "Согласовать срочную закупку крепежа"
    assert task.assignee == "Сергеева Анна"
    assert task.deadline == "12.03 чт"


def test_parse_pandoc_protocol_empty_meeting():
    empty = """\
ПРОТОКОЛ СОВЕЩАНИЯ

Отчет ОМС Пустого филиала

| **Дата:** | **пятница, 13 марта 2026 г.** |
|----|----|

Краткая сводка

| **Статус производства:** | **—** |
|----|----|
| Рекомендация: | Данные совещания отсутствуют |

Участники

| **№** | **ФИО** | **Роль** |
|-------|---------|----------|
| —     | Участники не определены | —        |

Повестка

\\_Повестка не сформирована\\_

Резюме

\\_Резюме не сформировано\\_

Задачи

| **№** | **Задача** | **Ответственный** | **Срок** | **Приоритет** |
|-------|------------|--------------------|----------|---------------|
| —     | —          | —                 | —        | —             |
"""
    pm = parse_protocol(empty)
    assert pm.title == "Отчет ОМС Пустого филиала"
    assert pm.meta["speakers"] == []
    assert pm.points == []
    assert pm.tasks == []
    assert pm.decisions == []


def test_ingest_meeting_pandoc_protocol_preserves_participants(tmp_path):
    """parse_nodul_payload must not wipe pandoc-table-extracted speakers when
    the webhook payload itself carries no `speakers` field (backfill path)."""
    from klemma.meetings import ingest_meeting
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    payload = {
        "meeting_id": "backfill-severny-20260310-0800",
        "date": "2026-03-10",
        "type": "ОМС",
        "site": "Северный филиал",
        "protocol_md": PANDOC_SAMPLE,
    }
    result = ingest_meeting(state, None, payload)
    src = state.get_source(result["source_id"])
    assert src["source_type"] == "meeting"
    meta = json.loads(src["meeting_meta"])
    assert meta["speakers"] == ["Сергеева Анна", "SPEAKER_00 [не идентифицирован]"]

    frags = state.get_fragments(source_id=result["source_id"], limit=1000)
    escalation_frags = [f for f in frags if f.get("citation_intent") == "escalation"]
    assert len(escalation_frags) == 1


def test_import_meeting_writes_db(tmp_path):
    """End-to-end DB write (no embeddings): source + fragments + meeting_meta."""
    from klemma.meetings import import_meeting
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "data" / "klemma.db"))
    pm = parse_protocol(SAMPLE)
    result = import_meeting(state, None, pm, "operativka")

    src = state.get_source(result["source_id"])
    assert src is not None
    assert src["source_type"] == "meeting"
    assert src["status"] == "completed"
    assert src["title"] == "Оперативка: отгрузки и логистика"

    # meeting_meta column was added by guarded ALTER and populated
    meta = json.loads(src["meeting_meta"])
    assert meta["type"] == "ОМС"
    assert meta["decisions"]

    frags = state.get_fragments(source_id=result["source_id"], limit=1000)
    assert len(frags) == result["fragments"] == 8  # 3 summary + 2 decision + 3 task

    # Idempotent re-import does not duplicate fragments (UNIQUE constraint)
    import_meeting(state, None, pm, "operativka")
    frags2 = state.get_fragments(source_id=result["source_id"], limit=1000)
    assert len(frags2) == 8
