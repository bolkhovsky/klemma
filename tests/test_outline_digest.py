"""Plan C3: condensed structure outline for the extraction prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from klemma.skills.outline_digest import (
    load_outline_digest,
    outline_hash,
    parse_structure_file,
    render_outline_digest,
    resolve_outline_path,
)

SAMPLE = """# Образцовая структура (редакция)

## 0. Что изменилось
- прочий текст без номера

## 1. Введение (проработка положений)
### 1.1. Положения, выносимые на защиту
1. **Модель** — не пункт структуры.

## 2. Глава 1. Анализ предметной области
### 1.1. Северный морской путь и потребности НГО
- 1.1.1. Судоходство по СМП: динамика, сезонность. Источники: @a, @b. Ч.
- 1.1.2. Кромка льда как навигационный объект; типовые решения. Н.
- 1.1.3. Ледовые карты ААНИИ (см. раздел 2.2). Г.
  - 1.1.3.1. Субъективность аналитика и «лучше больше льда» как источник
    неопределённости. Материал: REPORT.md. Г (рисунок).
### 1.2. Методы прогнозирования
- 1.2.1. Физические методы — обзор. Г/Ч.

## 3. Глава 2. Разработка модели
### 2.1. Концептуальная модель
- 2.1.1. Состав модели: поле, эталон. Н.

## Глава 3. Методика (без сквозного номера, редакция 05.09)
### 3.1. Процедура
- **3.1.1. Манифесты данных.** FTP OSI SAF, WDC ААНИИ; контрольные суммы.
- **3.1.2. Разбиение выборок.** Временной разрыв.

## 6. Заключение, приложения, список литературы
## 7. Перечень рисунков и таблиц
## 11. Открытые вопросы к руководителю
"""


def test_parse_ids_titles_levels_and_specials():
    p = parse_structure_file(SAMPLE)
    by_id = {i.id: i for i in p.items}
    assert [i.id for i in p.items if i.level == 2] == ["Глава 1", "Глава 2", "Глава 3"]
    assert by_id["3.1.1"].title == "Манифесты данных" and by_id["3.1.2"].title == "Разбиение выборок"
    assert by_id["1.1"].level == 3 and by_id["1.1"].title == "Северный морской путь и потребности НГО"
    assert by_id["1.1.1"].title == "Судоходство по СМП" and by_id["1.1.1"].level == 4
    assert by_id["1.1.2"].title == "Кромка льда как навигационный объект"
    assert by_id["1.1.3"].title == "Ледовые карты ААНИИ"
    assert by_id["1.1.3.1"].level == 5 and by_id["1.1.3.1"].title.startswith("Субъективность аналитика")
    assert by_id["1.2.1"].title == "Физические методы"  # status «Г/Ч.» and dash cut
    assert by_id["2.1.1"].chapter == 2
    assert p.numbered_item_ids == ["1.1.1", "1.1.2", "1.1.3", "1.1.3.1", "1.2.1", "2.1.1", "3.1.1", "3.1.2"]
    assert p.unparsed == []
    assert any("Введение" in s for s in p.sections)
    assert any("Заключение" in s for s in p.sections)
    assert not any("Открытые вопросы" in s for s in p.sections)


def test_unparsed_numbered_lines_are_reported():
    p = parse_structure_file(SAMPLE + "\n## 4. Глава 3. Методика\n- 3.1.1 без точки после номера\n")
    assert p.unparsed and "3.1.1" in p.unparsed[0][1]


def test_render_keeps_every_id_and_shrinks_titles_only():
    p = parse_structure_file(SAMPLE)
    full = render_outline_digest(p, max_chars=10_000)
    for i in p.numbered_item_ids + ["1.1", "1.2", "2.1"]:
        assert f"{i} " in full
    assert "Прочие разделы: " in full
    tight = render_outline_digest(p, max_chars=len(full) - 20, title_max=80)
    assert len(tight) < len(full)
    for i in p.numbered_item_ids:
        assert f"{i} " in tight  # ids survive, only titles shrink
    assert outline_hash(full) != outline_hash(tight) and len(outline_hash(full)) == 16


def test_resolve_outline_path_relative_to_project_root(tmp_path):
    assert resolve_outline_path(tmp_path, "Структура.md") == (tmp_path / "Структура.md").resolve()
    assert resolve_outline_path(tmp_path, str(tmp_path / "abs.md")) == tmp_path / "abs.md"
    assert resolve_outline_path(None, "x.md") is None
    assert resolve_outline_path(tmp_path, "") is None


def test_load_outline_digest_missing_file_is_empty(tmp_path, caplog):
    from types import SimpleNamespace

    assert load_outline_digest(tmp_path, SimpleNamespace(outline_file="")) == ""
    with caplog.at_level("WARNING"):
        assert load_outline_digest(tmp_path, SimpleNamespace(outline_file="nope.md")) == ""
    (tmp_path / "s.md").write_text(SAMPLE, encoding="utf-8")
    d = load_outline_digest(tmp_path, SimpleNamespace(outline_file="s.md", outline_max_chars=12000))
    assert "1.1.1 Судоходство по СМП" in d


def test_project_config_accepts_outline_file_from_frontmatter():
    from klemma.config import ProjectConfig

    cfg = ProjectConfig.model_validate({"type": "dissertation", "outline_file": "S.md"})
    assert cfg.outline_file == "S.md" and cfg.outline_max_chars == 12000


REAL = Path.home() / "research" / "dissertation" / "Структура_диссертации_v2.md"


@pytest.mark.skipif(not REAL.exists(), reason="dissertation structure not on this machine")
def test_acceptance_real_structure_has_101_unique_ids_and_fits_budget():
    p = parse_structure_file(REAL.read_text(encoding="utf-8"))
    ids = p.numbered_item_ids
    # The structure is a living document (101 items on 04.09.2026, 80 after the
    # author's cut on 05.09); the invariants are: nothing silently unparsed,
    # ids unique, all four chapters present, digest within budget.
    assert len(ids) >= 30 and len(set(ids)) == len(ids)
    assert p.unparsed == []
    assert [i.id for i in p.items if i.level == 2] == [f"Глава {n}" for n in (1, 2, 3, 4)]
    assert any("Введение" in s for s in p.sections) and any("Заключение" in s for s in p.sections)
    assert len(render_outline_digest(p)) <= 12_000
