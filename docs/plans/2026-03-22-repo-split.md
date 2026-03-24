# Plan: Monorepo → Two-repo split

**Дата:** 2026-03-22
**Статус:** Draft — ожидает утверждения

## Мотивация

1. Исследователи клонируют CLI и получают Docker + Vue + HTML (4370 LOC frontend, 2725 LOC API)
2. Код auth/API в публичном repo — риск поиска уязвимостей и копирования
3. Лицензионная путаница: Polyform NC + Proprietary в одном репо
4. SaaS-код растёт быстрее CLI — разные ритмы релизов

## Текущее состояние

```
bolkhovsky/klemma (monorepo)
├── src/klemma/           — CLI + Core (Polyform NC)
│   ├── api/              — FastAPI backend (2725 LOC) ← вынести
│   ├── stores/           — SQLite stores (shared)
│   ├── models.py         — UserRecord (shared)
│   └── protocols.py      — Interfaces (shared)
├── saas/
│   ├── dashboard/        — Vue 3 frontend (4370 LOC) ← вынести
│   └── deploy/           — Docker, Caddy, .env ← вынести
├── .github/workflows/
│   └── deploy.yml        — SaaS deploy ← вынести
└── tests/
    └── test_auth_api.py  — API tests ← вынести
```

**Зависимости API → Core (7 импортов):**
- `klemma.__version__`
- `klemma.models.UserRecord`
- `klemma.stores.{file_store, paper_store, project_store, user_library, user_store}`

**Обратные зависимости Core → API:** 0 (подтверждено grep)

## Целевая структура

### Repo 1: `bolkhovsky/klemma` (public, Polyform NC)

```
bolkhovsky/klemma
├── src/klemma/
│   ├── cli.py, commands/, skills/
│   ├── state.py, repositories/
│   ├── stores/              — остаётся (shared code, pip package)
│   ├── models.py            — остаётся
│   ├── protocols.py         — остаётся
│   ├── ai.py, embeddings.py, vault.py
│   └── (без api/)
├── tests/                    — без test_auth_api.py
├── prompts/
├── pyproject.toml            — без [api] extras
└── LICENSE                   — Polyform NC
```

### Repo 2: `bolkhovsky/klemma-saas` (private, Proprietary)

```
bolkhovsky/klemma-saas
├── api/                      — FastAPI (бывший src/klemma/api/)
│   ├── app.py
│   ├── routes/
│   ├── auth/
│   ├── tasks.py
│   ├── adapters.py
│   └── deps.py
├── dashboard/                — Vue 3 (бывший saas/dashboard/)
│   ├── src/views/
│   ├── src/components/
│   └── package.json
├── deploy/                   — Docker (бывший saas/deploy/)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── Caddyfile
├── tests/
│   └── test_auth_api.py
├── .github/workflows/
│   └── deploy.yml
├── pyproject.toml            — depends on klemma
└── LICENSE                   — Proprietary
```

## План миграции (10 шагов)

### Phase A: Подготовка (в текущем монорепо)

**Шаг 1: Убрать `api/` из klemma package namespace**

Сейчас API — это `klemma.api`, часть пакета `klemma`. После split API должен быть самостоятельным пакетом, импортирующим `klemma` как зависимость.

- Рефакторинг внутренних import'ов в `api/`: `from klemma.api.auth → from api.auth`
- Изменить `api/app.py`: вместо `from klemma.api.routes import ...` → `from api.routes import ...`
- Сохранить внешние import'ы: `from klemma.stores import ...` (это будет pip-зависимость)

**Шаг 2: Убрать `[api]` extras из klemma pyproject.toml**

- Удалить `api = [fastapi, uvicorn, argon2-cffi, PyJWT, ...]` из pyproject.toml
- Эти зависимости уйдут в klemma-saas/pyproject.toml

**Шаг 3: Отделить тесты**

- `test_auth_api.py` → пометить для переноса
- `test_user_store.py` — остаётся (UserStore используется и в CLI через stores/)

### Phase B: Создание нового репозитория

**Шаг 4: Создать `bolkhovsky/klemma-saas` (private)**

```bash
gh repo create bolkhovsky/klemma-saas --private --description "Klemma SaaS Portal"
```

**Шаг 5: Перенести файлы с сохранением git history**

Используем `git filter-repo` или ручной перенос (история не критична для SaaS — он молодой, 21 PR за 1 неделю):

```bash
# В klemma-saas:
mkdir -p api dashboard deploy tests .github/workflows

# Скопировать из монорепо:
cp -r klemma/src/klemma/api/* klemma-saas/api/
cp -r klemma/saas/dashboard/* klemma-saas/dashboard/
cp -r klemma/saas/deploy/* klemma-saas/deploy/
cp klemma/tests/test_auth_api.py klemma-saas/tests/
cp klemma/.github/workflows/deploy.yml klemma-saas/.github/workflows/
```

**Шаг 6: Создать pyproject.toml для klemma-saas**

```toml
[project]
name = "klemma-saas"
version = "0.1.0"
dependencies = [
    "klemma @ git+https://github.com/bolkhovsky/klemma.git",
    "fastapi==0.115.12",
    "uvicorn[standard]>=0.34",
    "argon2-cffi==23.1.0",
    "PyJWT[crypto]==2.10.1",
    "pydantic[email]>=2.0",
    "httpx>=0.27",
    "python-multipart>=0.0.9",
    "redis>=5.0",
    "rq>=2.0",
]
```

**Шаг 7: Обновить import paths в API**

```python
# Было (внутрипакетные):
from klemma.api.routes import auth, library, ...
from klemma.api.auth.tokens import create_access_token
from klemma.api.deps import get_current_user

# Стало (self-contained):
from api.routes import auth, library, ...
from api.auth.tokens import create_access_token
from api.deps import get_current_user

# Внешние (через pip) — БЕЗ ИЗМЕНЕНИЙ:
from klemma.stores.paper_store import LocalPaperStore
from klemma.models import UserRecord
```

**Шаг 8: Обновить Dockerfile**

```dockerfile
# Было:
COPY . /app
RUN pip install ".[api,recommended]"

# Стало:
COPY . /app
RUN pip install -e "."
# klemma ставится как зависимость из git автоматически
```

### Phase C: Очистка монорепо

**Шаг 9: Удалить SaaS-код из bolkhovsky/klemma**

```bash
# В основном репо:
rm -rf src/klemma/api/
rm -rf saas/
rm tests/test_auth_api.py
rm .github/workflows/deploy.yml
# Обновить pyproject.toml: убрать [api] extras
# Обновить CLAUDE.md: убрать api/ секции
```

**Шаг 10: Финализация**

- Обновить README.md в обоих репо
- Обновить CLAUDE.md: убрать api/ из klemma, создать новый в klemma-saas
- ADR-016: задокументировать решение о split
- Обновить memory files: architecture-decisions.md, product-vision.md
- Обновить GitHub Issues: закрыть SaaS issues в klemma, создать в klemma-saas
- Обновить deploy.yml CI/CD target
- Smoke test: `pip install klemma` + `pip install klemma-saas` + health check

## Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Сломается CI/CD | Высокая | Smoke test после каждого шага |
| Забытые import paths | Средняя | grep по `klemma.api` в обоих репо |
| Не тот klemma version в portal | Низкая | Pin конкретный commit hash или tag |
| SaaS issues в старом репо | Низкая | Batch-перенос через `gh issue transfer` |

## Оценка трудозатрат

| Шаг | Усилие | Блокеры |
|-----|--------|---------|
| 1-3 (подготовка) | 1-2 часа | Нет |
| 4-6 (новый репо) | 30 мин | Нет |
| 7-8 (import refactor) | 1-2 часа | Нет |
| 9-10 (очистка + docs) | 1 час | Нет |
| **Итого** | **~4-5 часов** | — |

## Что НЕ нужно делать

- ❌ Не переносить git history (SaaS слишком молод, 1 неделя)
- ❌ Не публиковать klemma на PyPI (git install достаточно)
- ❌ Не менять структуру stores/ (shared code остаётся в klemma)
- ❌ Не разделять tests/ на отдельный пакет
- ❌ Не трогать CLI commands, skills, repositories
