# Klemma: Руководство пользователя

## 1. Введение

Klemma — AI-ассистент для академического письма. Он автоматизирует рутинную работу с литературой: извлекает цитируемые фрагменты из PDF, классифицирует их по типу (фон, метод, результат), отслеживает покрытие разделов источниками, находит пробелы в библиографии, ищет семантически похожие работы и генерирует исследовательские брифинги.

**Для кого**: PhD-студенты, исследователи, научные работники — все, кто работает с большим количеством источников при написании диссертации, статьи или монографии.

**Ключевые возможности**:
- Автоматическое извлечение фрагментов из PDF с классификацией citation intent
- Отслеживание покрытия глав/разделов источниками
- Семантический поиск похожих источников (SPECTER embeddings)
- Reference gap detection — какие ссылки из библиографий отсутствуют в вашей библиотеке
- Citation graph analysis — кто кого цитирует, co-citation, author network
- AI-powered research briefings, библиотечный аудит, ежедневное планирование
- Вложенные проекты (диссертация + отдельные статьи)

**Как работает**: Zotero (библиотека) → BetterBibTeX (JSON-экспорт) → klemma (AI-анализ PDF) → SQLite (фрагменты, статистика) + Obsidian vault (заметки).

---

## 2. Установка и настройка

### 2.1 Требования

- **Python 3.11+**
- **Zotero** с плагином [BetterBibTeX](https://retorque.re/zotero-better-bibtex/) — для автоматического JSON-экспорта библиотеки
- **Obsidian** vault — хранение заметок по источникам и отчётов
- **AI-бэкенд** — Claude Code CLI (по умолчанию), или OpenAI/Ollama/LiteLLM

### 2.2 Установка

```bash
pip install -e .                          # базовая
pip install -e ".[embeddings]"            # + семантический поиск (рекомендуется)
pip install -e ".[openai]"               # + OpenAI/Ollama бэкенд
pip install -e ".[local-embeddings]"     # + офлайн SPECTER2
```

### 2.3 Настройка BetterBibTeX

1. В Zotero: Edit → Preferences → Better BibTeX → Automatic Export
2. Добавить экспорт: формат **Better CSL JSON**, путь — запомнить (например `~/Zotero/library.json`)
3. Автообновление: "On Change"

Этот файл klemma использует для поиска PDF по citekey.

### 2.4 Инициализация проекта

```bash
cd ~/research/my-thesis/
klemma init
```

Мастер задаст вопросы:
- Тип проекта (dissertation / paper / thesis)
- Путь к Obsidian vault (обнаруживается автоматически)
- Путь к BetterBibTeX JSON
- AI-бэкенд

Создаётся:
```
my-thesis/
├── KLEMMA.md          # контекст проекта для AI (редактируемый)
└── .klemma/
    ├── config.yaml    # конфигурация
    ├── tags.yaml      # таксономия тегов
    └── data/
        └── klemma.db  # SQLite база
```

### 2.5 KLEMMA.md — контекст для AI

Файл `KLEMMA.md` в корне проекта описывает ваше исследование для AI. Чем лучше заполнен, тем точнее результаты.

```markdown
# Моё исследование

## Тема
Валидация нейросетевых прогнозов ледовой обстановки

## Структура
- Глава 1: Анализ предметной области
  - 1.1 Спутниковые данные (AMSR2, SMOS)
  - 1.2 Существующие модели (IceNet, SEAS5)
- Глава 2: Методология валидации
  ...

## Научные результаты
- НР1: Модель валидации прогнозов (RMSE, skill score)
- НР2: Сравнительный анализ нейросетей vs физических моделей

## Текущий фокус
Раздел 2.3 — архитектура IceNet и её модификации
```

### 2.6 Настройка embeddings (рекомендуется)

Добавьте в `.klemma/config.yaml`:

```yaml
embeddings:
  backend: "s2"       # бесплатный Semantic Scholar API
```

Это включает семантический поиск (`similar`), гибридное обнаружение источников и semantic gap scoring. Бэкенды:

| Бэкенд | Стоимость | Требования | Качество |
|--------|-----------|------------|----------|
| `s2` | Бесплатно | Интернет | Хорошее (SPECTER 768-dim) |
| `local` | Бесплатно | GPU, `[local-embeddings]` | Хорошее (SPECTER2) |
| `openai` | Платно | API key, `[openai]` | Отличное (1536-dim) |

### 2.7 Дополнительные AI-настройки

В `~/.klemma/config.yaml` (или проектном):

```yaml
ai:
  backend: "claude"
  language: "ru"           # язык AI-ответов (по умолчанию "ru")
  json_mode: false         # structured JSON output (для бэкендов, поддерживающих JSON mode)
```

`language` влияет на язык всех AI-генерированных отчётов (план, брифинг, аудит). `json_mode` полезен при интеграции с OpenAI-совместимыми бэкендами, поддерживающими structured output.

### 2.8 Куда сохраняются отчёты

- AI-отчёты (`outline`, `research`, `library`, `ask`) → корень проекта (`project_root/`)
- Vault-заметки `@citekey.md` → папка `obsidian.notes_folder` в vault

Это значит, что research briefing для раздела 2.3 окажется в `~/research/my-thesis/`, а заметка по источнику — в `~/vault/2 - Refs/@smithML2020.md`.

---

## 3. Основные понятия

### Sources (источники)
Записи из Zotero. Каждый источник проходит пайплайн: `pending` → `processing` → `completed`. При обработке извлекаются фрагменты и создаётся vault-заметка.

### Fragments (фрагменты)
Цитируемые отрывки из PDF, привязанные к главам/разделам. Каждый фрагмент имеет:
- **citation_intent**: `background` (фон), `method` (методология), `result_comparison` (сравнение результатов)
- **relevance** (1-5): насколько релевантен разделу
- **section**: к какому разделу привязан

### Coverage (покрытие)
Количество источников на раздел. Минимальный порог задаётся в config (`min_sources_per_section: 3`). Разделы с недостаточным покрытием выделяются красным в `klemma status`.

### Reference Gaps
Ссылки из библиографий обработанных источников, отсутствующие в вашей библиотеке. Scoring учитывает citation intent:
- **method** gaps: вес 3x (критичные — методология не покрыта)
- **result_comparison** gaps: вес 2x (важные — нечем сравнивать)
- **background** gaps: вес 1x (желательно, но не критично)

### Embeddings
Числовые векторы (768 или 1536 измерений), представляющие содержание статьи. Используются для поиска семантически похожих работ, даже если у них нет общих ключевых слов.

### Citation Graph
Кто кого цитирует. Строится автоматически при `process` из key_references аннотации. Позволяет анализировать co-citation (какие работы часто цитируются вместе) и author network.

---

## 4. Рабочие процессы

### 4.1 Добавление источников

**Основной путь** — через Zotero:
1. Добавляете PDF в Zotero (вручную, через browser connector, или импортом)
2. BetterBibTeX автоматически обновляет JSON-экспорт
3. Источник появляется в klemma при следующей команде

**Быстрое добавление** — через CLI:
```bash
klemma acquire https://arxiv.org/pdf/2101.12345.pdf
klemma acquire <url> --title "Title" --authors "Smith, J." --year 2023 --section 2.3
```

**Массовый импорт**:
```bash
klemma acquire --batch papers.json
```

### 4.2 Обработка источников

Обработка — главная операция klemma. Превращает PDF в структурированные данные.

```bash
# Обработать все pending (рекомендуется — параллельно, 3 потока)
klemma process

# Один конкретный источник
klemma process smithMachineLearning2020

# Последовательно (при лимитах API)
klemma process --serial
```

**Что происходит при `process`**:
1. Находит PDF (BBT lookup → Zotero storage → fuzzy search)
2. Извлекает текст (PyMuPDF, до 50K символов)
3. AI-анализ: scaffold prompting → фрагменты с citation intent → vault note
4. Сохраняет фрагменты в SQLite
5. Создаёт `@citekey.md` в Obsidian (summary, methodology, key references)
6. Записывает reference gaps + citation links
7. Генерирует embedding (если настроен бэкенд)

**Совет**: обрабатывайте пакетами (накопите 5-10 источников, затем `klemma process`).

### 4.3 Отслеживание прогресса

```bash
# Быстрая проверка
klemma status
```

Показывает:
- Обработано / pending / failed источников
- Покрытие по главам (цветовая индикация)
- Разделы с недостаточным покрытием
- Топ reference gaps

```bash
# Детальная аналитика
klemma status --verbose
```

Дополнительно показывает:
- **Intent coverage matrix** — распределение background/method/result по разделам. Если раздел 2.3 имеет 0 method-фрагментов, значит методология не покрыта цитатами
- **Embedding stats** — сколько источников имеют embeddings
- **Citation graph stats** — количество цитатных связей, наиболее цитируемые внешние работы

```bash
# Фокус на одной главе
klemma status --chapter 2
```

### 4.4 Работа с разделом

Когда нужно написать конкретный раздел:

```bash
# 1. Глубокий анализ раздела
klemma research -s 2.3.1
```

Генерирует research briefing:
- Структура аргументации
- План цитирования (какой фрагмент куда)
- Пробелы и рекомендации
- Связи между источниками

**Инкрементальный режим**: после первого запуска briefing сохраняется в `project_root/`. При повторном запуске:

1. Откройте файл, добавьте заметки в секцию `## ✏️ Что нового`
2. Запустите `klemma research -s 2.3.1` снова
3. Klemma определит дельту (новые источники/фрагменты) и обновит briefing
4. Ваши заметки архивируются в `## 📋 История изменений`

```bash
# Принудительно переизвлечь все фрагменты
klemma research -s 2.3.1 --force
```

### 4.5 Анализ библиотеки

Три режима — от быстрого до глубокого:

```bash
# Общее здоровье
klemma library
```
Покрытие, качество, критические проблемы.

```bash
# Рекомендации для раздела
klemma library -s 2.3
```
Порядок чтения, оценка источников, что добавить.

```bash
# Глубокий аудит
klemma library --audit
```
Полный анализ:
- Дублирование и устаревшие источники
- Пробелы в методологии
- **Co-citation analysis**: какие работы часто цитируются вместе
- **Author network**: исследовательские группы (авторы с 2+ работами)
- **Prune рекомендации**: что можно убрать

Просмотр prune-рекомендаций:
```bash
klemma library prune               # все рекомендации
klemma library prune -v drop       # только "drop"
klemma library prune -c 2          # для главы 2
klemma library prune --clear key   # отменить рекомендацию
```

### 4.6 Семантический поиск

Требует настроенный `embeddings.backend`.

```bash
# 1. Сгенерировать embeddings (один раз, потом инкрементально)
klemma embed
klemma embed --dry-run             # сколько будет обработано

# 2. Найти похожие на конкретный источник
klemma similar smithML2020
```

Результат — таблица с cosine similarity. Помогает найти работы, которые не поймал keyword-поиск.

```bash
# 3. Найти источники для раздела
klemma similar 2.3
```

Вычисляет "центроид" раздела (среднее embedding всех привязанных источников) и ищет **похожие источники из других разделов**. Обнаруживает скрытые связи — например, статья привязана к главе 1, но семантически близка к разделу 3.2.

**Гибридный поиск**: при обработке (`process`) и research-брифингах klemma автоматически комбинирует keyword-поиск (40%) + semantic similarity (60%).

### 4.7 Структура проекта (outline)

```bash
# Первый запуск — AI генерирует outline из файлов + базы
klemma outline

# С директивой
klemma outline -p "Акцент на экспериментальной части"

# Полная перегенерация
klemma outline --fresh
```

Инкрементальный режим работает аналогично `research`: добавьте заметки в `## ✏️ Что нового`, запустите повторно.

### 4.8 Интерактивный агент

Для сложных вопросов, требующих контекста:

```bash
klemma ask "Какие статьи используют SPECTER embeddings для citation recommendation?"
klemma ask -s 2.3 "Сравни подходы к валидации в моих источниках"
klemma ask -ch 1 "Найди пробелы в литературном обзоре"
```

Агент получает полный контекст проекта (outline, источники, фрагменты, покрытие, пробелы) и может использовать инструменты (веб-поиск, файлы). Ответы сохраняются в `project_root/`.

---

## 5. Продвинутые возможности

### 5.1 Вложенные проекты

Для диссертации с отдельными статьями:

```bash
cd ~/research/my-thesis/
klemma init                         # диссертация

cd paper_ice/
klemma init --type paper            # статья (наследует vault/zotero)
```

Статья использует свою БД, но vault и Zotero диссертации. AI видит контекст обоих проектов.

```bash
klemma info                         # project chain
klemma tree                         # дерево вложенности
```

### 5.2 AI-бэкенды

```yaml
# Claude (по умолчанию) — лучший анализ
ai:
  backend: "claude"

# OpenAI
ai:
  backend: "openai"
  model: "gpt-4o"
  api_key_env: "OPENAI_API_KEY"

# Ollama (локально)
ai:
  backend: "openai"
  base_url: "http://localhost:11434/v1"
  model: "llama3.1"

# LiteLLM (100+ провайдеров)
ai:
  backend: "litellm"
  model: "anthropic/claude-sonnet-4-20250514"
```

### 5.3 Кастомные промпты

Промпты можно переопределить на уровне проекта или системы:

```
.klemma/prompts/extract.md          # переопределение для проекта
~/.klemma/prompts/extract.md        # переопределение глобально
```

Доступные шаблоны: `extract.md`, `annotate.md`, `morning.md`, `research.md`, `research_incremental.md`, `librarian.md`, `librarian_prune.md`, `agent.md`, `outline.md`, `outline_incremental.md`, `analyst.md`, `reconstruct.md`.

### 5.4 MCP-сервер для embeddings

Klemma включает SPECTER MCP-сервер для интеграции embeddings с другими инструментами:

```bash
pip install klemma[mcp]
python -m klemma.tools.specter_server           # S2 бэкенд
python -m klemma.tools.specter_server --local   # локальный SPECTER2
```

Предоставляет MCP tools: `embed_paper`, `find_similar`, `batch_embed`, `get_citation_intents`.

### 5.5 Бенчмарк и ручная разметка (`klemma benchmark`)

Команда `klemma benchmark` позволяет измерить точность четырёх ключевых функций klemma:
- **Intent classifier** — правильно ли AI определяет тип цитирования (background / method / result_comparison)
- **Gap scoring** — насколько хорошо ранжируются недостающие источники по важности
- **Embedding retrieval** — насколько семантически похожие источники оказываются в топе поиска
- **Citation reconstruction** — может ли AI восстановить карту цитирования статьи, не видя текста

Чтобы провести бенчмарк, нужен **аннотированный датасет** — JSON-файл, в котором вы (эксперт) сами проставили правильные ответы. Klemma сравнивает свои предсказания с вашими и считает метрики.

#### Полный рабочий процесс

**Шаг 1. Обработать выбранные источники (если нет `citation_intent` в БД)**

```bash
klemma process cohanSPECTERDocumentlevelRepresentation2020 \
               valenzuelaIdentifyingMeaningfulCitations \
               cohanStructuralScaffoldsCitation2019 \
               --serial
```

Флаг `--serial` показывает прогресс по каждому источнику. Для уже обработанных источников используйте `--force`, чтобы переизвлечь фрагменты с обновлённым промптом:

```bash
klemma process --force --serial cohanSPECTERDocumentlevelRepresentation2020
```

**Шаг 2. Экспортировать шаблон датасета**

```bash
klemma benchmark --export dataset.json
```

Команда создаёт `dataset.json`, заполняя его фрагментами из БД, у которых уже есть `citation_intent`. В поле `ground_truth` пока стоит значение, которое проставил AI. Это **черновик, а не финальный датасет** — его нужно проверить вручную.

**Шаг 3. Аннотировать датасет вручную** ← *это и есть «разметка»*

Подробно описано ниже.

**Шаг 4. Запустить бенчмарк**

```bash
klemma benchmark -d dataset.json --metrics intent
klemma benchmark -d dataset.json --metrics all
klemma benchmark -d dataset.json --metrics all --json-output
klemma benchmark -d dataset.json --semantic          # гибридный keyword × semantic gap scoring
klemma benchmark -d dataset.json --reconstruct       # citation reconstruction
```

---

#### Citation reconstruction benchmark

Бенчмарк citation reconstruction проверяет, может ли AI восстановить карту цитирования статьи по одним лишь метаданным (title, abstract) — без доступа к тексту. Это задача, близкая к реальному use case: «какие работы должна цитировать статья на эту тему?»

**Шаг 1. Извлечь ground truth из PDF**

```bash
klemma benchmark --analyst smithML2020
```

Команда `--analyst` читает PDF статьи и с помощью AI (промпт `analyst.md`) извлекает список цитируемых работ с intent-классификацией. Результат сохраняется в формате reconstruction dataset.

**Шаг 2. Запустить reconstruction бенчмарк**

```bash
klemma benchmark -d dataset.json --reconstruct
```

Сравнивает два подхода:
- **Baseline** — ранжирование по citation graph (co-citation, общие авторы)
- **AI-driven** — AI рекомендует цитирования по title/abstract (промпт `reconstruct.md`)

Метрики: Precision@K, Recall@K, nDCG@K, F1.

---

#### Автоматический пайплайн

Для полностью автономного бенчмаркинга:

```bash
klemma benchmark --candidates                        # показать кандидатов
klemma benchmark --prepare smithML2020               # подготовить недостающие ссылки
klemma benchmark --auto                              # полный пайплайн: select → prepare → analyst → benchmark → persist
klemma benchmark --auto --paper smithML2020           # указать конкретную статью
klemma benchmark --auto --skip-prepare               # пропустить подготовку ссылок
```

`--candidates` ранжирует обработанные статьи по пригодности для бенчмарка (количество in-library цитирований, разнообразие intent, наличие PDF).

`--auto` автоматически выбирает лучшего кандидата, готовит данные, запускает analyst + reconstruction и сохраняет результаты в историю.

---

#### История запусков

Klemma сохраняет каждый запуск бенчмарка в SQLite для отслеживания прогресса:

```bash
klemma benchmark --history                           # список прошлых запусков
klemma benchmark --compare id1 id2                   # сравнить два запуска
klemma benchmark --export-history history.json        # экспортировать историю как JSON
```

---

#### Что такое ручная разметка `ground_truth`

После экспорта `dataset.json` выглядит примерно так:

```json
{
  "version": "1.0",
  "fragments": [
    {
      "source_id": "cohanSPECTERDocumentlevelRepresentation2020",
      "fragment_text": "We compute SPECTER embeddings for each source using title and abstract, storing the 768-dimensional vector for cosine similarity retrieval.",
      "ground_truth": "method"
    },
    {
      "source_id": "kastrinRecentAdvancesFuture2025",
      "fragment_text": "Kastrin et al. provide a comprehensive survey of LBD systems, noting that citation-aware retrieval remains an open challenge.",
      "ground_truth": "method"
    }
  ],
  "gaps": [ ... ]
}
```

Поле `ground_truth` заполнено AI-предсказанием. Проблема: если использовать его как эталон без проверки, бенчмарк будет сравнивать AI с самим собой — и всегда получит 100%. Это ничего не измеряет.

**Ваша задача:** открыть файл в текстовом редакторе, прочитать каждый фрагмент и решить, правильно ли AI определил тип цитирования. Если нет — исправить `ground_truth`.

---

#### Три типа цитирования — как их различать

| Тип | Когда используется | Сигнальные фразы |
|---|---|---|
| `background` | Источник цитируется, чтобы ввести контекст, определить понятие или сослаться на обзор предметной области | «Ранее показано, что…», «X — широко используемый подход…», «Опираясь на работу…», «Согласно…» |
| `method` | Конкретная техника, модель или алгоритм из источника **применяется** в вашей работе напрямую | «Мы используем X для…», «Следуя подходу…», «Адаптируя метод из…», «Реализовано на основе…» |
| `result_comparison` | Числовые результаты или конкретные выводы из источника **сравниваются** с вашими | «X достигает 87%…», «В отличие от Y, который…», «Превосходя Z на…» |

**Правило большого пальца:** задайте себе вопрос — *зачем в тексте диссертации стоит эта ссылка?*
- Чтобы объяснить читателю, о чём идёт речь → `background`
- Потому что именно этот метод используется → `method`
- Потому что с этой работой сравниваются результаты → `result_comparison`

---

#### Примеры разметки

**Пример 1 — AI угадал правильно, оставляем:**

```
Фрагмент: "We compute SPECTER embeddings for each source using title and abstract,
           storing the 768-dimensional vector for cosine similarity retrieval."
AI сказал: "method"
Ваше решение: SPECTER здесь применяется напрямую как техника → оставляем "method" ✓
```

**Пример 2 — AI ошибся, исправляем:**

```
Фрагмент: "Kastrin et al. provide a comprehensive survey of LBD systems,
           noting that citation-aware retrieval remains an open challenge."
AI сказал: "method"
Ваше решение: это обзорная ссылка, никакой техники из неё не применяется →
              меняем на "background"
```

**Пример 3 — фрагмент неоднозначный, удаляем:**

```
Фрагмент: "Citation intent classification was introduced by Valenzuela et al."
Проблема: неясно — это просто историческая справка (background) или
          их метод используется (method)?
Ваше решение: удалить запись из "fragments" → лучше меньше чистых примеров,
              чем шум в датасете
```

---

#### Как бенчмарк считает метрики

После сохранения аннотированного файла `klemma benchmark -d dataset.json --metrics intent`:

1. Для каждого фрагмента в `fragments` ищет в БД совпадение по `source_id` и первым 80 символам текста
2. Читает `citation_intent`, который AI записал в БД при обработке
3. Сравнивает с вашим `ground_truth`
4. Считает **macro-F1** — основную метрику (равный вес каждому классу, не зависит от дисбаланса классов)
5. Дополнительно: точность (accuracy), precision/recall/F1 по каждому классу, матрица ошибок

Пример вывода:

```
Intent Benchmark
  Total annotated:   45
  Matched in DB:     43
  Skipped:            2

  macro_f1:    0.74
  accuracy:    0.79

  Per-class:
    background:        P=0.85  R=0.91  F1=0.88
    method:            P=0.70  R=0.68  F1=0.69
    result_comparison: P=0.58  R=0.50  F1=0.54
```

Низкий F1 для `result_comparison` — типичная картина: таких цитирований мало, и они сложнее всего для классификации. Именно поэтому используется macro-F1 (а не accuracy) — accuracy 79% может скрывать полный провал на редких классах.

---

## 6. Рекомендуемый ежедневный workflow

### Утро
```bash
klemma plan                         # план на день
```

### Работа с разделом
```bash
klemma research -s 2.3.1            # briefing
# ... пишете текст, добавляете заметки ...
klemma research -s 2.3.1            # инкрементальное обновление
```

### Добавление источников
```bash
# Добавляете PDF в Zotero...
klemma process                      # обработать pending
klemma embed                        # сгенерировать embeddings
```

### Конец дня
```bash
klemma status                       # проверить прогресс
klemma similar 2.3                  # обнаружить связи
```

### Еженедельно
```bash
klemma library --audit              # аудит библиотеки
klemma library prune                # просмотреть рекомендации
klemma outline                      # обновить структуру
```

---

## 7. Решение проблем

### PDF не найден

1. Обновлён ли BetterBibTeX JSON-экспорт? (проверить дату файла)
2. Правильный ли `zotero.library_json` в config?
3. PDF ищется в 3 этапа: прямой путь → BBT lookup → fuzzy search в Zotero storage
4. Попробуйте `klemma process <citekey>` для конкретного источника — покажет путь поиска

### Embeddings не работают

1. Установлен ли `pip install klemma[embeddings]`?
2. Настроен ли `embeddings.backend` в config?
3. `s2` бэкенд: проверьте интернет-соединение (S2 API может быть rate-limited)
4. `local` бэкенд: установлен ли `klemma[local-embeddings]`?
5. `openai` бэкенд: задан ли `OPENAI_API_KEY`?
6. `klemma embed --dry-run` — проверить, сколько источников готово к embedding

### Источник в статусе "failed"

```bash
klemma status --verbose            # проверить error_message
```

Частые причины:
- PDF повреждён или защищён паролем
- AI-бэкенд недоступен (проверьте `claude --version` или API key)
- Timeout: увеличьте `ai.timeout` в config (по умолчанию 180 сек)
- Слишком короткий PDF: порог `processing.min_pdf_length: 500` символов

### Низкое покрытие раздела

1. `klemma status -ch N --verbose` — какие разделы проблемные
2. `klemma research -s X.X` — AI найдёт пробелы
3. `klemma library -s X.X` — рекомендации что добавить
4. `klemma similar X.X` — семантически похожие из других разделов
5. Reference gaps table — какие работы цитируют ваши источники, но их нет в библиотеке

### Вложенные проекты не работают

1. Родительская директория имеет `.klemma/`?
2. `obsidian.vault_path` настроен в родителе?
3. `klemma info` — проверить project chain
4. `klemma tree` — визуализация структуры
