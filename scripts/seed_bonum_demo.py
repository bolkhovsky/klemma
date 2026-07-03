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

Then run the API with:
    KLEMMA_BONUM_PROJECT_ROOT=~/klemma-bonum-demo \
    KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 \
    KLEMMA_DATA_DIR=~/.klemma \
    uvicorn klemma.api.app:create_app --factory
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the repo without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from klemma.meetings import (  # noqa: E402
    BONUM_ROOT_ENV,
    build_state_and_embeddings,
    import_meeting,
    parse_protocol,
)

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


def seed_meetings(root: Path, seed_dir: Path) -> dict:
    paths = write_protocols(seed_dir)
    state, embeddings = build_state_and_embeddings(root)
    if embeddings is None:
        print("⚠ No embedding backend resolved — importing without vectors "
              "(semantic search / ask will be empty).")
    total_frags = total_emb = 0
    for p in paths:
        pm = parse_protocol(p.read_text(encoding="utf-8"))
        r = import_meeting(state, embeddings, pm, p.stem)
        total_frags += r["fragments"]
        total_emb += r["embedded"]
        print(f"  ✓ {r['source_id']}: {r['fragments']} fragments, {r['embedded']} embedded")
    return {"meetings": len(paths), "fragments": total_frags, "embedded": total_emb}


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
    args = ap.parse_args()

    import os

    root = Path(args.root).expanduser()
    seed_dir = Path(__file__).resolve().parent / "seed_bonum_meetings"

    print(f"→ Importing meetings into {root}/.klemma/data/klemma.db")
    stats = seed_meetings(root, seed_dir)
    print(f"  Imported {stats['meetings']} meetings, {stats['fragments']} fragments, "
          f"{stats['embedded']} embedded.\n")

    project_id = "<run with SaaS>"
    if not args.no_saas:
        data_dir = Path(
            args.data_dir or os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
        ).expanduser()
        print(f"→ Creating SaaS user + project in {data_dir}/users.db")
        try:
            user_id, project_id = seed_saas(data_dir, args.email, args.password)
            print(f"  user_id={user_id}  project_id={project_id}\n")
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
