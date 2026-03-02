# CiteQ.ru — MVP Roadmap

**Продукт:** веб-сервис проверки качества цитирования диссертаций
**Домен:** citeq.ru
**Аудитория:** аспиранты, докторанты, научные руководители
**Формат:** Web UI + REST API
**Основа:** klemma v0.4.1 (CLI → SaaS)

---

## 1. Продуктовая концепция

### Ценностное предложение

> Загрузи текст диссертации + библиографию → получи карту покрытия, пробелы в литературном обзоре, анализ citation intent и рекомендации по доработке. За 5 минут вместо 2 недель ручного аудита.

### User Journey (MVP)

```
1. Регистрация (email/password)
2. Создать проект → указать тему, структуру глав
3. Загрузить файлы:
   - Текст диссертации (PDF/DOCX/LaTeX)
   - Список литературы (BibTeX / ручной ввод / PDF)
   - Опционально: PDF источников
4. Обработка (async):
   - Извлечение фрагментов из загруженных PDF
   - Классификация citation intent
   - Анализ покрытия по главам/разделам
   - Поиск пробелов (reference gaps)
5. Результаты:
   - Дашборд: покрытие, пробелы, quality score
   - Рекомендации по доработке (librarian analysis)
   - Исследовательский брифинг по разделу
   - Экспорт отчёта (PDF/Markdown)
```

### Отличие от антиплагиат-сервисов

CiteQ — это НЕ проверка на плагиат. Это **аудит качества научной базы**:
- Антиплагиат проверяет: «ты скопировал чужой текст?»
- CiteQ проверяет: «ты опираешься на достаточное количество релевантных источников?»

---

## 2. Анализ стека бэкенда

### Вариант A: FastAPI + SQLite (максимум переиспользования klemma)

| Параметр | Оценка |
|----------|--------|
| Переиспользование klemma | **95%** — StateManager, все 8 repositories, skills, AI providers, prompts работают as-is |
| Время до MVP | **6-8 недель** |
| Масштабирование | до ~500 concurrent users (per-user SQLite + Litestream backup) |
| Auth | добавить вручную (authlib / JWT) |
| Деплой | Docker: 1 контейнер FastAPI + 1 Redis/ARQ (task queue) |
| Миграция на PostgreSQL | потребует рефакторинга state.py позже |

**Архитектура:**
```
[Nginx] → [FastAPI + Uvicorn]
              ├── klemma-core (skills, AI, extraction)
              ├── per-user SQLite (data/users/<user_id>/project.db)
              ├── Object Storage (PDFs: S3/MinIO/local)
              └── [ARQ/Redis] → worker (async processing)
```

### Вариант B: FastAPI + PostgreSQL (масштабируемый с нуля)

| Параметр | Оценка |
|----------|--------|
| Переиспользование klemma | **60%** — skills и AI переиспользуются, repositories нужно переписать на SQLAlchemy |
| Время до MVP | **10-14 недель** |
| Масштабирование | тысячи пользователей, горизонтальное масштабирование |
| Auth | добавить вручную |
| Деплой | Docker: FastAPI + PostgreSQL + Redis |
| Будущее | production-ready с первого дня |

### Вариант C: PocketBase + Python AI Engine

| Параметр | Оценка |
|----------|--------|
| Переиспользование klemma | **50%** — только skills и AI, вся data layer заменяется PocketBase collections |
| Время до MVP | **4-6 недель** (auth/admin из коробки) |
| Масштабирование | Зависит от PocketBase (SQLite внутри, но с REST API) |
| Auth | из коробки (email, OAuth2) |
| Деплой | Docker: PocketBase (Go) + Python AI Engine |
| Компромисс | два рантайма (Go + Python), нужен мост |

### Рекомендация: Вариант A → B (эволюционный)

**Стартуем с FastAPI + SQLite** (максимум кода от klemma), **мигрируем на PostgreSQL** при достижении ~200 пользователей.

Аргументы:
1. klemma's StateManager + 8 repositories — это ~1500 строк отлаженного кода с миграциями. Переписывать на старте — потеря времени.
2. Per-user SQLite — проверенный паттерн (Notion, Expensify, Fly.io все используют).
3. FastAPI даёт полный контроль и нативный Python-стек.
4. Auth через `python-jose` + `passlib` — 200 строк кода на JWT.
5. Litestream обеспечивает S3-бэкап SQLite в реальном времени.

---

## 3. Переиспользование klemma-core

### Что берём as-is (создаём `klemma-core` package)

| Модуль | Строк | Что делает для web |
|--------|-------|--------------------|
| `state.py` + `repositories/` | ~1500 | Полная data layer: sources, fragments, gaps, citations, embeddings, plans |
| `skills/extractor.py` | 215 | Извлечение фрагментов из PDF → фрагменты + citation intent |
| `skills/researcher.py` | 830 | Research briefing по разделу |
| `skills/librarian.py` | 522 | Library health, recommendations, audit |
| `skills/outliner.py` | 296 | Генерация структуры диссертации |
| `skills/planner.py` | 248 | Утренние планы / рекомендации |
| `skills/agent.py` | 230 | Fragment RAG для Q&A |
| `ai.py` + `ai_litellm.py` | 580 | AI providers (LiteLLM → 100+ backends) |
| `embeddings.py` | 268 | SPECTER/OpenAI embeddings |
| `literature/pdf.py` | ~200 | PDF парсинг (PyMuPDF) |
| `literature/models.py` | ~100 | BibTeX/BBT модели |
| `config.py` (частично) | ~300 | Pydantic config models |
| `prompts/` | все | Jinja2 шаблоны — сердце AI логики |

**Итого переиспользуется: ~5000 строк кода** из ~7000 строк klemma.

### Что заменяем

| Модуль klemma | Заменяется на | Причина |
|---------------|---------------|---------|
| `cli.py` (2993) | FastAPI routes | CLI → HTTP API |
| `vault.py` (263) | `WebStorageAdapter` | Obsidian → S3/local FS |
| `library_provider.py` (86) | `UploadLibrary` | Локальный BBT JSON → загрузка BibTeX |
| `setup.py` (304) | Web onboarding | Интерактивный wizard → web-форма |
| `discovery.py` (260) | Не нужен | Нет локальных файлов |
| `app.py` + `tui/` | Frontend SPA | TUI → web UI |

### Новый слой: `WebContext` (замена `KlemmaContext`)

```python
@dataclass
class WebContext:
    """All dependencies for a single API request."""
    config: ProjectConfig      # per-project settings
    state: StateManager        # per-user SQLite (как в klemma)
    storage: StorageAdapter    # S3/local FS (замена vault)
    ai: AIProvider             # LiteLLM (как в klemma)
    embeddings: EmbeddingProvider | None
    user_id: str
    project_id: str
    dissertation_context: str
    available_tags: list[str]
```

---

## 4. Архитектура MVP

```
┌────────────────────────────────────────────────────────────┐
│                     Frontend (SPA)                          │
│  React / Next.js (или Vue/Nuxt)                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │ Dashboard │ │ Upload   │ │ Research │ │ Settings │     │
│  │ (покрытие│ │ (PDF,    │ │ (briefing│ │ (проект, │     │
│  │  пробелы)│ │  BibTeX) │ │  по разд)│ │  AI key) │     │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
└───────────────────────┬────────────────────────────────────┘
                        │ HTTPS / REST API
┌───────────────────────┴────────────────────────────────────┐
│                    FastAPI Backend                           │
│                                                             │
│  Auth (JWT)  │  Routes:                                     │
│  ┌─────────┐ │  POST /projects         — создать проект     │
│  │ Register│ │  POST /projects/{id}/upload — загрузить файлы│
│  │ Login   │ │  POST /projects/{id}/process — запуск анализа│
│  │ JWT     │ │  GET  /projects/{id}/status — дашборд        │
│  └─────────┘ │  GET  /projects/{id}/research/{section}      │
│              │  GET  /projects/{id}/library — health report  │
│              │  POST /projects/{id}/ask — Fragment RAG       │
│              │  GET  /projects/{id}/export — PDF отчёт       │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              klemma-core (reused)                     │   │
│  │  StateManager │ Skills │ AI │ Embeddings │ Prompts   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Storage:                                                   │
│  ├── data/users/{user_id}/projects/{project_id}/            │
│  │   ├── klemma.db        (SQLite — per-project)            │
│  │   ├── uploads/         (PDF, BibTeX)                     │
│  │   └── reports/         (generated reports)               │
│  └── S3 (optional, via Litestream for backup)               │
│                                                             │
│  Task Queue: [ARQ + Redis]                                  │
│  └── Workers: process_sources, generate_research,           │
│               analyze_library, generate_embeddings           │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. MVP Scope (8 недель)

### Phase 1: Foundation (недели 1-2)

**Цель: работающий бэкенд с auth и загрузкой файлов**

- [ ] Выделить `klemma-core` package из klemma (skills, AI, state, prompts)
- [ ] `StorageAdapter` — абстракция над файловым хранилищем (заменяет VaultAdapter)
- [ ] `WebContext` — замена KlemmaContext для web-режима
- [ ] FastAPI skeleton: project structure, settings, CORS
- [ ] Auth: register/login/JWT (python-jose + passlib + bcrypt)
- [ ] User model в отдельном SQLite (`data/auth.db`)
- [ ] CRUD проектов: create, list, get, delete
- [ ] File upload endpoint: PDF + BibTeX
- [ ] BibTeX parser (замена BBT JSON — стандартный `.bib` парсинг)
- [ ] Docker Compose: FastAPI + Redis

### Phase 2: Core Processing (недели 3-4)

**Цель: загрузка файлов → автоматический анализ**

- [ ] ARQ task queue setup (Redis)
- [ ] Worker: `process_source` — извлечение фрагментов из PDF (переиспользует `extractor.py`)
- [ ] Worker: `parse_dissertation` — извлечение структуры из текста диссертации
- [ ] Auto-register sources из загруженного BibTeX → StateManager
- [ ] Auto-process: после загрузки запускать extraction pipeline
- [ ] API: GET /status — покрытие, пробелы, quality score (переиспользует state.get_coverage_stats, get_gap_summary)
- [ ] WebSocket/SSE для прогресса обработки (real-time updates)
- [ ] API key management: пользователь вводит свой OpenAI/Anthropic ключ

### Phase 3: AI Analysis (недели 5-6)

**Цель: AI-powered инсайты**

- [ ] Worker: `research_section` — исследовательский брифинг (переиспользует `researcher.py`)
- [ ] Worker: `library_analysis` — health report (переиспользует `librarian.py`)
- [ ] API: POST /ask — Fragment RAG (переиспользует `agent.py`)
- [ ] Worker: `generate_embeddings` — SPECTER/OpenAI (переиспользует `embeddings.py`)
- [ ] API: GET /similar — семантический поиск похожих источников
- [ ] Report export: Markdown → PDF (WeasyPrint или pdfkit)
- [ ] Rate limiting по API keys и per-user quotas

### Phase 4: Frontend + Polish (недели 7-8)

**Цель: полноценный web UI**

- [ ] Frontend SPA (React + Tailwind + shadcn/ui)
- [ ] Страницы: регистрация, вход, список проектов, дашборд проекта
- [ ] Дашборд: визуализация покрытия (bar chart по главам), таблица пробелов, quality score
- [ ] Upload flow: drag-and-drop PDF/BibTeX, прогресс-бар
- [ ] Research briefing view: per-section AI анализ
- [ ] Library health report view
- [ ] Ask (RAG chat): чат-интерфейс для вопросов по библиотеке
- [ ] Landing page: citeq.ru (ценностное предложение, демо, цены)
- [ ] Деплой: VPS (Hetzner/Timeweb) + Caddy + Docker Compose

---

## 6. Монетизация (post-MVP)

### Freemium модель

| План | Цена | Лимиты |
|------|------|--------|
| **Free** | 0 руб | 1 проект, 20 источников, 5 AI-запросов/день |
| **Аспирант** | 490 руб/мес | 3 проекта, 200 источников, 50 AI-запросов/день |
| **Pro** | 990 руб/мес | неогр. проектов, неогр. источников, 200 AI-запросов/день, приоритетная обработка |
| **Кафедра** | 4 900 руб/мес | 20 пользователей, общая библиотека, admin panel |

### BYOK (Bring Your Own Key)

Free-план разрешает BYOK — пользователь вводит свой API-ключ (OpenAI/Anthropic), citeq предоставляет инфраструктуру бесплатно. Это снижает барьер входа и стоимость серверных AI-затрат.

---

## 7. Tech Stack (финальный)

| Слой | Технология | Обоснование |
|------|-----------|-------------|
| **Backend** | FastAPI + Uvicorn | Нативный Python, async, OpenAPI docs из коробки |
| **Data** | SQLite (per-project) | Прямое переиспользование klemma StateManager |
| **Auth** | python-jose + passlib | Минимальный JWT, 200 LOC |
| **Task Queue** | ARQ + Redis | Async Python workers, лёгкий |
| **AI** | LiteLLM (from klemma) | 100+ providers, BYOK поддержка |
| **Storage** | Local FS → S3 (MinIO) | PDF хранение, миграция позже |
| **Frontend** | React + Tailwind + shadcn/ui | Быстрая разработка, компонентная библиотека |
| **PDF Export** | WeasyPrint | HTML → PDF для отчётов |
| **Deploy** | Docker Compose + Caddy | Let's Encrypt, reverse proxy |
| **Hosting** | Hetzner Cloud (CX22) | 4 vCPU, 8GB RAM, ~8 EUR/мес, EU datacenter |
| **Backup** | Litestream → S3 | Реальтайм SQLite бэкап |
| **Monitoring** | Sentry + Prometheus | Ошибки + метрики |

---

## 8. Риски и митигация

| Риск | Вероятность | Митигация |
|------|------------|-----------|
| Стоимость AI-вызовов на сервере | Высокая | BYOK модель — пользователь платит за своё использование |
| SQLite не справится с нагрузкой | Средняя | Per-project изоляция + Litestream; миграция на PG при 200+ users |
| Парсинг произвольных PDF | Высокая | PyMuPDF (уже в klemma) + fallback на OCR (Tesseract) для сканов |
| Конкуренция с антиплагиат | Низкая | Разные задачи — CiteQ про качество, не про плагиат |
| BibTeX парсинг (не BBT JSON) | Средняя | Библиотека `bibtexparser` — зрелая, покрывает edge cases |
| Безопасность API-ключей | Высокая | Шифрование at-rest (Fernet), передача только через HTTPS |

---

## 9. Метрики успеха MVP

| Метрика | Цель (3 мес после запуска) |
|---------|---------------------------|
| Регистрации | 200+ |
| Активные проекты | 50+ |
| Конверсия Free → Paid | 5-10% |
| Обработанных источников | 5 000+ |
| NPS | > 40 |
| Uptime | > 99.5% |

---

## 10. Post-MVP Roadmap

1. **Telegram-бот** — загрузка PDF и получение отчётов прямо в чате
2. **Коллаборация** — shared проекты для науч. руководитель + аспирант
3. **Semantic Scholar интеграция** — автоматический поиск и предложение недостающих источников
4. **LaTeX-плагин** — интеграция в Overleaf / VS Code
5. **API для вузов** — массовая проверка диссертаций кафедрой
6. **Citation graph visualization** — интерактивный граф цитирования (D3.js)
7. **Мультиязычность** — English UI для международного рынка

---

## 11. Структура репозитория (план)

```
citeq/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app
│   │   ├── auth/                # JWT auth
│   │   ├── routes/              # API endpoints
│   │   ├── workers/             # ARQ task workers
│   │   ├── storage/             # StorageAdapter (local/S3)
│   │   └── models/              # Pydantic request/response models
│   ├── klemma_core/             # extracted from klemma
│   │   ├── state.py             # StateManager (as-is)
│   │   ├── repositories/        # 8 repos (as-is)
│   │   ├── skills/              # all skills (adapted)
│   │   ├── ai.py                # AI providers (as-is)
│   │   ├── embeddings.py        # (as-is)
│   │   ├── literature/          # PDF parsing (as-is)
│   │   └── prompts/             # Jinja2 templates (as-is)
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/               # React pages
│   │   ├── components/          # UI components
│   │   └── api/                 # API client
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── Caddyfile
└── README.md
```
