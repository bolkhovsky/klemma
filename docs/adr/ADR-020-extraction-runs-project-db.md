# ADR-020 Extraction runs, attempts and the active set

**Status**: Accepted
**Date**: 2026-09-04
**Plan**: dissertation plan C1–C2 (`~/.claude/plans/pure-inventing-piglet.md`, ред. 6)
**Predecessors**: ADR-014 (three-tier split), ADR-019 (claim provenance substrate)
**Supersedes**: ADR-019 §6 for run/attempt provenance only

## Context

Повторное извлечение фрагментов было разрушающим (`process --force` удалял
корпус источника до вызова модели) и непрослеживаемым: в `klemma.db` нет ни
промпта, ни модели, ни параметров чанкования у фрагмента; глобальная
`extractions` (`UNIQUE(paper_id, prompt_hash, ai_model)`, один `extraction_id`
на фрагмент) не выражает повторные попытки — разные параметры чанкования при
том же промпте сталкиваются, фрагмент не может принадлежать двум попыткам.
Issue #372 описывает то же для SaaS.

Одновременно план докфудинга диссертации требует: старые выписки сохранять,
новые прогоны публиковать только полными и проверенными, а «активный набор»
фрагментов делать проектно- и пользовательско-зависимым (один и тот же
глобальный фрагмент может быть активен в одном проекте и нет в другом).
ADR-019 §6 держал субстрат в legacy `klemma.db`; для прогонов это решение
не подходит, потому что секции и активный набор принадлежат проекту.

Измерено 04.09.2026 на библиотеке диссертации: монолит 408 источников /
2500 фрагментов; глобальная `library.db` уже содержит 3854 фрагмента для 403
из них после старой автомиграции (402 статьи с синтетическим хэшем);
`project.db` v5 — 440 источников, 0 `project_fragments`.

## Decision

### 1. Чистый движок, персистентность снаружи (C1)
`skills/extract_engine.extract_from_pages` — страницы → `ExtractionOutcome`,
без БД и vault. Покрытие считается объединением интервалов успешных листовых
чанков; обрыв ответа (`finish_reason=max_tokens`) или неразборный JSON делят
чанк по границе предложения с ограниченным перекрытием; бюджет резервируется
до вызова (в том числе перед починкой JSON) и списывается оценкой, если
бэкенд не сообщает расход; тексты длиннее порога валидации получают
`validation_incomplete`. Обёртки (`extractor.extract_fragments`,
`api.tasks._run_chunked_extraction`) владеют записью.

### 2. Размещение по трёхуровневой схеме (C2)
- **library.db (PaperStore)**: `extraction_attempts` (UUID попытки,
  `request_fingerprint`, промпт/модель/версии/параметры/покрытие) и
  `extraction_attempt_fragments` (span, локатор, статус дословности).
  Канонические `fragments` не меняются и не удаляются. Таблицы создаются
  идемпотентно: `user_version` файла принадлежит `LocalUserLibrary` и не
  трогается. Старая `extractions` новым путём не пишется.
- **project.db (ProjectStore) v6**: `project_extraction_runs` (условия запуска
  дублируются — failed-run воспроизводим без строки в library.db),
  `project_run_fragments` (снимок relevance/usage_hint/model_section),
  `project_fragments` с PK `(user_id, citekey, fragment_id)` и полями
  `curated_section` / `legacy_section` / `section_origin`,
  `project_sources.active_run_id`.
- **klemma.db**: совместимость и миграция; новые таблицы не добавляются.

### 3. Идентичность
`attempt_id = uuid4()` на каждую фактическую попытку (три одинаковых запуска —
три попытки; переиспользуется только при `--resume-stale`).
`request_fingerprint = sha256(paper_id, source_content_hash, hash
отрендеренного промпта, outline_hash, ai_model, klemma_version,
extractor_version, канонический config_json)` — для поиска повторов и
eval-отчёта, хранится и в попытке.

### 4. Протокол публикации
Шаг 0 — до первого вызова модели: строка `running` в
`project_extraction_runs` и попытка в library.db. Шаг 1 — идемпотентная
запись library.db. Шаг 2 — одна транзакция project.db: связи, upsert
`project_fragments` (без `curated_section`), обновление строки прогона и —
только для полного и проверенного прогона — переключение `active_run_id`;
перед этим проверяется существование каждого `fragment_id` в попытке;
нарушение — откат и `failed, error=integrity`. Шаг 3 — vault, вне
транзакции. Сбой между шагами 1 и 2 оставляет orphan-попытку без ссылок —
безопасно, перечисляется `repair --scan`.

### 5. Состояния
Ортогональные флаги `is_partial` и `validation_incomplete`; статусы
`running → pending | published | published_partial | failed | discarded`.
Активный набор переключается автоматически только на `published`;
`published_partial` — только явной командой
`process --activate-partial --run N --reason …` при
`validation_incomplete=0`; `repair --run N` снимает `validation_incomplete`
и публикует полный прогон. Legacy-секция монолита копируется в
`legacy_section` с `section_origin='legacy_unknown'`: происхождение не
угадывается (`reassign_skips` хранит отклонённые предложения, применённые
переназначения истории не имеют). Эффективная секция:
`COALESCE(curated_section, model_section активного прогона, legacy_section)`.

### 6. Миграция
Одна реализация `klemma.migration.migrate_monolith` для команды
`migrate-library` и автомиграции: идентичность статьи по реальному
`pdf_hash` → DOI → citekey в `user_sources` → `migrated:<citekey>` → новая;
переносятся все поля, spans, локаторы, эмбеддинги; legacy-попытка на
`(paper, citekey)`; `project_sources` для всех источников; реестр CSV на
каждую входную строку; dry-run даёт те же N_* без записи.

## Consequences

- `process --force` больше не разрушает: старые выписки сохраняются в сторах
  и в истории прогонов; `--replace` удаляет только проектные строки без
  связей с прогонами, глобальные строки library.db не удаляются никогда.
- Частичный результат не попадает в общий library-кэш и не регистрируется
  как completed; источник помечается degraded (`extraction`).
- **Долг**: монолит `klemma.db` остаётся источником чтения для `research`,
  RAG, `draft`, `check-citations`; в него по-прежнему пишется объединение
  старых и новых фрагментов (INSERT OR IGNORE), а не активный набор.
  Активный набор читается через `ProjectStore.get_project_fragments` и
  `klemma source show`. Перевод читающих команд на сторы — отдельная работа.
- **Долг**: SaaS-обёртка не использует lifecycle прогонов (issue #372
  остаётся открытым для library.db-модели продвижения).
- Тесты: `test_extract_engine.py`, `test_extraction_runs.py`,
  `test_migration.py`, `test_repair_runs.py`; store-тесты обновлены до v6.
