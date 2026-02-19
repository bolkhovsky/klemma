# План академической статьи о системе Klemma

## Метаданные

- **Название:** «Klemma: архитектура AI-ассистента для управления научной литературой в процессе написания диссертации»
- **Тип:** системная статья для конференции (6-10 стр.)
- **Язык:** русский
- **Целевые площадки:**
  - AIST (Analysis of Images, Social Networks and Texts) — Springer CCIS, Scopus
  - ДИАЛОГ (Computational Linguistics and Intellectual Technologies) — Scopus
  - RCDL (Российская конференция по электронным библиотекам) — если фокус на управлении данными

---

## Структура статьи

### 1. Введение (~1 стр.)

- Проблема: управление 100-200+ источниками — ручной, фрагментированный процесс (Zotero отдельно, PDF-ридер отдельно, заметки отдельно)
- Разрыв: менеджеры ссылок (Zotero, Mendeley) не анализируют содержание; AI-инструменты (Elicit, Semantic Scholar) не интегрированы со структурой конкретной диссертации; PKM-системы (Obsidian) требуют ручного ввода
- Вклад Klemma: первая система, сочетающая (a) AI-извлечение фрагментов с привязкой к разделам, (b) отслеживание пробелов в библиографии, (c) MCP-расширяемость, (d) инкрементальные брифинги
- Масштаб: 174 источника, 2340+ фрагментов, 3+ месяца ежедневного использования

### 2. Обзор существующих решений (~1.5 стр.)

- **2.1 Менеджеры ссылок:** Zotero [1], Mendeley [2] — метаданные и PDF, не анализ содержания. BetterBibTeX как мост к внешним инструментам
- **2.2 AI для академических исследований:**
  - *Коммерческие:* Elicit (QA по статьям), Semantic Scholar [5,6] (TLDR [28], графы), Research Rabbit (визуальное открытие)
  - *Open-source RAG:* PaperQA [29] (RAG с цитатами, сверхчеловеческая точность в научном QA), LitLLM (генерация related work)
  - *Автономные агенты:* GPT-Researcher [31] (глубокий веб-поиск, мульти-агентная архитектура), STORM [30] (мульти-перспективная генерация статей, Stanford), AI-Scientist [32] (полный цикл: гипотеза → эксперимент → статья)
  - *Zotero + AI:* PapersGPT (Zotero-плагин с Claude/GPT, BM25-поиск), Aria (Zotero+GPT), Oracle of Zotero (PaperQA+ZoteroDB)
  - **Общий разрыв:** все работают с отдельными статьями или веб, не с библиотекой + структурой конкретной диссертации
- **2.3 PKM + AI:** Obsidian + Zettelkasten [17,18]. AI-плагины: obsidian-copilot (агентный AI), Smart Connections (эмбеддинги). obsidian-zotero-integration (мост метаданных, однонаправленный). Нет автоматического AI-извлечения из PDF
- **2.4 MCP-экосистема для академии:** 10+ MCP-серверов arXiv, несколько для Zotero (54yyyu/zotero-mcp), Semantic Scholar, Scopus, PubMed. Все — одиночные мосты к API; нет интегрированного registry + hybrid pipeline
- **2.5 PDF-извлечение:** GROBID [33] (ML-парсинг структуры), MinerU [34] (PDF→markdown для LLM), science-parse (Allen AI). Низкоуровневое; нет AI-аннотации с контекстом диссертации
- **Таблица 1:** сравнение Klemma vs PaperQA vs GPT-Researcher vs STORM vs PapersGPT vs Obsidian+AI по 11 параметрам:

| Функция | Klemma | PaperQA | GPT-Res. | STORM | PapersGPT | Obsidian+AI |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Привязка к разделам дисс. | **+** | - | - | - | - | - |
| Reference gap tracking | **+** | - | - | - | - | - |
| Авто-разрешение пробелов | **+** | - | - | - | - | - |
| Гибридный discovery (MCP+LLM) | **+** | - | - | - | - | - |
| Инкрементальные брифинги | **+** | - | - | - | - | - |
| MCP ToolRegistry | **+** | - | - | - | - | - |
| Obsidian vault | **+** | - | - | - | - | **+** |
| Zotero библиотека | **+** | - | - | - | **+** | **+** |
| AI-извлечение из PDF | **+** | **+** (RAG) | **+** (веб) | - | **+** (chat) | - |
| SQLite state machine | **+** | **+** | - | - | - | - |
| Ежедневное планирование | **+** | - | - | - | - | - |

### 3. Архитектура системы (~2 стр.)

- **3.1 Общая архитектура:** Config (Pydantic) → State (SQLite) → Skills (AI) → Output (CLI/TUI/Obsidian). KlemmaContext dataclass. Двойной режим CLI/TUI
- **3.2 Слой данных:** SQLite WAL (8 таблиц), Pydantic-модели конфигурации, VaultAdapter
- **3.3 Протокол LibraryProvider:** LocalLibrary (BBT JSON) / MCPLibrary (zotero-mcp), graceful fallback
- **3.4 Архитектура AI-навыков:** 5 навыков (planner, extractor, researcher, librarian, agent), паттерн context→prompt→LLM→JSON→persist. Jinja2-шаблоны экстернализированы
- **3.5 MCP-интеграция:** MCPClient (sync обёртка async SDK), ToolRegistry, stdio-транспорт, stateless-соединения
- **Листинг 1:** KlemmaContext dataclass
- **Листинг 2:** LibraryProvider protocol

### 4. Ключевые компоненты и потоки данных (~2.5 стр.)

- **4.1 Конвейер извлечения фрагментов:** PDF → PyMuPDF → Jinja2+контекст → Claude → JSON → SQLite + Obsidian. 3-уровневый поиск PDF. Авто-создание vault-заметок через note_factory
- **4.2 Отслеживание пробелов в библиографии:** AI-анализ библиографии каждого источника → кросс-проверка с библиотекой (174 записи) → reference_gaps. Формула: `count × avg_quality × section_weight`. Авто-разрешение по автору+году
- **4.3 Гибридный discovery-конвейер:** Фаза 1 (MCP-поиск по gaps + keywords) → Фаза 2 (Claude: оценка, тип использования, приоритет) → discoveries таблица. Фоновый режим
- **4.4 Инкрементальные брифинги:** Полный режим → инкрементальный (дельта: новые источники, фрагменты, пользовательские заметки). Архивирование с timestamp
- **4.5 Синхронизация разделов:** ~60мс для 138 заметок, 30+ regex-паттернов, детекция переименований через itemKey
- **Листинг 3:** extract_fragments() (сокращённый)
- **Листинг 4:** Скоринг reference gaps (SQL)

### 5. Практическое применение (~1.5 стр.)

- **5.1 Контекст:** диссертация по ледовой обстановке в Арктике, 4 главы, 174 источника
- **5.2 Количественные метрики:** таблица (источники, фрагменты, время обработки, sync, regex-паттерны, таблицы SQLite)
- **5.3 Качественная оценка:** ускорение 10-15x (2 мин vs 20-30 мин), обнаружение неочевидных пробелов, структурные рекомендации для глав
- **5.4 Ограничения:** зависимость от Claude CLI, качество OCR, хрупкость regex, однопользовательский режим, нет формальной оценки precision/recall

### 6. Заключение (~0.5 стр.)

- Итоги: паттерн AI-навыков, reference gap tracking, MCP для академии, инкрементальные брифинги
- Будущее: RAG по фрагментам, прямой API, коллаборативный режим, формальная оценка

---

## Иллюстрации (4 шт.)

### Рис. 1. Общая архитектура системы
Слоёная диаграмма:
- **Верхний слой:** Интерфейсы пользователя (CLI: 10 команд, TUI: 5 экранов)
- **Средний слой:** AI-навыки (Planner, Extractor, Researcher, Librarian, Agent) ← Jinja2-промпты (7 шаблонов)
- **Ядро:** KlemmaContext (config, state, vault, ai, library, tools)
- **Нижний слой:** Внешние системы (Zotero/BBT JSON, Obsidian vault, Claude Code CLI, MCP-серверы)

### Рис. 2. Конвейер извлечения фрагментов
Горизонтальная блок-схема:
```
PDF файл → PyMuPDF (текст + маркеры страниц)
  → [Jinja2: extract.md + метаданные + контекст диссертации]
    → Claude Code CLI → JSON {fragments[]}
      → SQLite fragments
      → Obsidian @citekey.md (секция "Цитаты для диссертации")
                ↓ (если заметки нет)
      → note_factory → [annotate.md] → AI-аннотация → создание заметки + reference_gaps
```

### Рис. 3. Цикл отслеживания пробелов в библиографии
Циклическая диаграмма:
```
Аннотация источника → AI-анализ библиографии (кросс-проверка с 174 записями)
  → reference_gaps (open) → score = count × avg_quality × section_weight
    → Discovery-конвейер (Фаза 1: MCP-поиск + Фаза 2: Claude-оценка)
      → discoveries (pending → accepted)
        → Добавление в Zotero → авто-resolve gaps
```

### Рис. 4. ER-диаграмма SQLite
```
sources (PK: id) ─1:N→ fragments (FK: source_id)
                  ─N:M→ source_sections (FK: source_id, PK: source_id+section)
                  ─1:N→ reference_gaps (FK: source_id)
                  ─1:1→ reading_queue (FK: source_id)
                  ─1:1→ prune_verdicts (FK: source_id)
discoveries (standalone, keyed by section)
daily_plans (standalone, keyed by date)
```

---

## Ключевые точки новизны (для рецензентов)

1. **AI-извлечение фрагментов с привязкой к разделам** — не просто «извлечь цитаты», а привязать к разделу 2.3.1 с оценкой релевантности и подсказкой по использованию
2. **Reference gap tracking с авто-разрешением** — новый механизм, отсутствующий во всех существующих менеджерах ссылок
3. **MCP для академических инструментов** — одна из первых реализаций Model Context Protocol в контексте научного исследования
4. **Обобщаемый паттерн AI-навыков** (context → Jinja2 prompt → LLM → JSON → persist) — архитектурный вклад для любых LLM-powered академических инструментов
5. **Инкрементальные брифинги** с дельта-вычислением и архивированием пользовательских заметок — отражение реального итеративного workflow

---

## Файлы для листингов кода

| Что показать | Файл | Строки |
|---|---|---|
| KlemmaContext | `src/klemma/context.py` | 17-31 |
| LibraryProvider protocol | `src/klemma/library_provider.py` | 22-39 |
| extract_fragments() | `src/klemma/skills/extractor.py` | 29-104 |
| Скоринг reference gaps | `src/klemma/state.py` | 756-793 |
| MCPClient | `src/klemma/tools/client.py` | 36-133 |
| Discovery pipeline | `src/klemma/tools/discovery.py` | 19-85 |

---

## Рекомендуемый список литературы (36 источников)

### Системы управления ссылками (4)
1. Stillman D. et al. Zotero: Personal research assistant // zotero.org, 2006-2024
2. Zaugg H. et al. Mendeley: Creating communities of scholarly inquiry // TechTrends, 55(1), 2011
3. Fenner M. Reference management // Opening Science, Springer, 2014
4. Pautasso M. Ten simple rules for writing a literature review // PLoS Computational Biology, 9(7), 2013

### AI для академических исследований (7)
5. Lo K. et al. S2ORC: The Semantic Scholar Open Research Corpus // ACL, 2020
6. Kinney R. et al. The Semantic Scholar Academic Graph // arXiv:2301.10140, 2023
7. Taylor R. et al. Galactica: A Large Language Model for Science // arXiv:2211.09085, 2022
8. Lewis P. et al. Retrieval-Augmented Generation for knowledge-intensive NLP tasks // NeurIPS, 2020
9. Ji Z. et al. Survey of hallucination in natural language generation // ACM Comp. Surveys, 55(12), 2023
10. Wadden D. et al. SciFact: Verifying scientific claims with abstracts // EMNLP, 2020
11. Naik R. et al. Literature-based discovery: Models, methods, and trends // J. Biomedical Informatics, 2022

### Model Context Protocol и tool-use в LLM (5)
12. Anthropic. Model Context Protocol specification // modelcontextprotocol.io, 2024
13. Schick T. et al. Toolformer: Language models can teach themselves to use tools // NeurIPS, 2023
14. Qin Y. et al. Tool learning with foundation models // Nature Machine Intelligence, 6, 2024
15. Mialon G. et al. Augmented language models: a survey // TMLR, 2023
16. Patil S. et al. Gorilla: Large language model connected with massive APIs // arXiv:2305.15334, 2023

### Управление знаниями / PKM (4)
17. Luhmann N. Kommunikation mit Zettelkästen // 1981
18. Ahrens S. How to Take Smart Notes // Suhrkamp, 2017
19. Bush V. As we may think // The Atlantic Monthly, 176(1), 1945
20. Conklin J. Hypertext: An introduction and survey // Computer, 20(9), 1987

### Промпт-инжиниринг (4)
21. Wei J. et al. Chain-of-thought prompting elicits reasoning in large language models // NeurIPS, 2022
22. Khattab O. et al. DSPy: Compiling declarative language model calls into pipelines // ICLR, 2024
23. Dong Q. et al. A survey on in-context learning // arXiv:2301.00234, 2023
24. Wang X. et al. Self-consistency improves chain of thought reasoning // ICLR, 2023

### Близкие системы и документные представления (4)
25. Cohan A. et al. SPECTER: Document-level representation learning // ACL, 2020
26. Hope T. et al. SciCo: Hierarchical cross-document coreference for scientific concepts // EMNLP, 2022
27. Singh A. et al. SciRepEval: A multi-format benchmark for scientific document representations // EMNLP, 2023
28. Cachola I. et al. TLDR: Extreme summarization of scientific documents // EMNLP (Findings), 2020

### Близкие open-source системы (4) — по результатам GitHub-анализа
29. Lala A., White A. et al. PaperQA: Retrieval-Augmented Generative Agent for Scientific Research // arXiv:2312.07559, 2023
30. Shao Z. et al. Assisting in Writing Wikipedia-like Articles From Scratch with Large Language Models (STORM) // arXiv:2402.14207, 2024
31. Assafelovic E. GPT Researcher: Autonomous Agent for Comprehensive Online Research // gptr.dev, 2024
32. Lu C. et al. The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery // arXiv:2408.06292, 2024

### Извлечение из научных документов (2)
33. Lopez P. GROBID: Combining automatic bibliographic data recognition and term extraction // ECDL, 2009
34. Shen Z. et al. MinerU: An Open-Source Solution for Precise Document Content Extraction // arXiv, 2024

### Русскоязычные источники (2)
35. Воронцов К.В. и др. Обзор методов машинного обучения для анализа научных текстов // Программная инженерия, 2023
36. Добров Б.В. и др. Автоматическая обработка текстов на естественном языке и анализа данных. — М.: НИУ ВШЭ, 2021
