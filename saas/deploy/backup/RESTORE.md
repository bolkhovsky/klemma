# Восстановление (C2)

Проверено вживую 30.07.2026: снапшот `/opt/backups` → расшифровка
офф-машинной копии на fram → новый том на VPS → одноразовый контейнер на
`127.0.0.1` (не тронул прод) → `POST /meetings/ingest` реальным токеном вернул
`200` с тем же `source_id`, что и в проде; число встреч осталось 4
(идемпотентное обновление, не дубль); `PRAGMA integrity_check` — `ok` на всех
четырёх базах.

Учение входит в приёмку C2 только целиком: одних баз недостаточно — без
`KLEMMA_JWT_SECRET`/`KLEMMA_BONUM_INGEST_TOKEN` восстановленный контур не
примет ни одного запроса. `.env` в бэкап не входит и не должен — секреты
хранятся в менеджере паролей, не на fram и не в репозитории.

## Когда это нужно

Прод-том `klemma-mi_klemma-mi-data` (или `deploy_bonum-data` /
`deploy_klemma-data`) утерян или повреждён.

## Шаг 1. Найти актуальный снапшот

**Локальный (на VPS, `/opt/backups`, ротация 7 дней) — предпочтительно, если
VPS жив:**

```bash
ssh klemma 'ls -la /opt/backups/ | grep <источник>'   # klemma-mi- / bonum- / litresearch-
```

**Офф-машинный (на fram, зашифрован `age`) — если VPS/диск утрачен целиком:**

```bash
ssh fram 'ls -la ~/backups/vps/ | grep <источник>'
```

Расшифровать (приватный ключ — из менеджера паролей, **не** копировать его на
диск фрам постоянно):

```bash
# на любой машине с установленным age
age -d -i <путь-к-приватному-ключу> -o klemma-mi-klemma.db klemma-mi-klemma-<дата>.db.age
# повторить для library / project / users
```

## Шаг 2. Поднять контур на новом томе (не трогая прод)

Пример для `klemma-mi` — для `bonum`/`litresearch` пути внутри тома другие
(см. `backup.py::SOURCES`).

```bash
VOL=klemma-mi-restore-<дата>
docker volume create "$VOL"

docker run --rm -v "$VOL":/data -v <каталог-с-расшифрованными-db>:/src:ro alpine sh -c '
  mkdir -p /data/meetings/.klemma/data /data/saas
  cp /src/klemma-mi-klemma.db   /data/meetings/.klemma/data/klemma.db
  cp /src/klemma-mi-library.db  /data/saas/library.db
  cp /src/klemma-mi-project.db  /data/saas/project.db
  cp /src/klemma-mi-users.db    /data/saas/users.db
'

source /opt/klemma-mi/.env   # секреты — только с прод-сервера, не хардкодить

docker run -d --rm \
  --name klemma-mi-restore-test \
  -v "$VOL":/data \
  --network deploy_default \
  -p 127.0.0.1:18001:8000 \
  -e KLEMMA_ENV=production \
  -e KLEMMA_DATA_DIR=/data/saas \
  -e KLEMMA_BONUM_PROJECT_ROOT=/data/meetings \
  -e KLEMMA_PROMPTS_DIR=/app/prompts \
  -e KLEMMA_SERVE_SPA=/app/dashboard \
  -e KLEMMA_JWT_SECRET="$KLEMMA_JWT_SECRET" \
  -e KLEMMA_BONUM_INGEST_TOKEN="$KLEMMA_BONUM_INGEST_TOKEN" \
  -e KLEMMA_DISABLE_REGISTRATION=1 \
  -e KLEMMA_CORS_ORIGINS=http://127.0.0.1:18001 \
  -e KLEMMA_EMBEDDINGS_BACKEND=litellm \
  -e KLEMMA_EMBEDDINGS_MODEL=ollama/bge-m3 \
  -e KLEMMA_EMBEDDINGS_BASE_URL=http://ollama:11434 \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  klemma-mi-portal:latest

sleep 4
curl -s http://127.0.0.1:18001/health   # {"status":"ok",...}
```

`-p 127.0.0.1:18001:8000` — важно: без Caddy, без публичного адреса. Прод
`klemma.bolkhovsky.ru` этот контейнер не задевает вообще.

## Шаг 3. Приёмка — POST /meetings/ingest, не просто список встреч

Список встреч в интерфейсе открылся бы даже с неверным `KLEMMA_JWT_SECRET`,
если знать пароль пользователя — это доказывает только то, что базы читаемы.
`POST /meetings/ingest` требует ПРАВИЛЬНЫЙ `X-Ingest-Token` **и** существующий
`site_slug` из восстановленного `portal_access`/`portal_sites` — успешный ответ
доказывает, что вернулись и данные, и секреты, и права одновременно.

```bash
curl -s -w "\nHTTP:%{http_code}\n" -X POST http://127.0.0.1:18001/meetings/ingest \
  -H "X-Ingest-Token: $KLEMMA_BONUM_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_id": "<существующий meeting_id без префикса mtg->",
    "date": "<дата встречи YYYY-MM-DD>",
    "site_slug": "<существующий слаг площадки>",
    "protocol_md": "**Заголовок**\n\n**Супер краткое содержание**:\n- Пункт протокола. [0:00]\n"
  }'
```

Ожидаемо: `HTTP:200`, `source_id` в ответе совпадает с существующим id
(идемпотентное обновление — число встреч в базе не растёт). 422 с текстом
«refusing to ingest an empty protocol» означает, что `protocol_md` не
распарсился ни в один фрагмент — нужен непустой текст в формате
`**Супер краткое содержание**:\n- пункт. [таймкод]` (см. `meetings.py::parse_protocol`),
а не отсутствие данных.

Число встреч до/после (должно совпасть):

```bash
docker exec klemma-mi-restore-test python3 -c "
import sqlite3
c = sqlite3.connect('/data/meetings/.klemma/data/klemma.db')
print(c.execute(\"SELECT COUNT(*) FROM sources WHERE source_type='meeting'\").fetchone()[0])
"
```

## Шаг 4. Целостность файлов

```bash
for f in klemma-mi-klemma klemma-mi-library klemma-mi-project klemma-mi-users; do
  python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0])" "${f}.db"
done
# → ok на каждой
```

## Шаг 5. Вычистить тест (или продвинуть в прод)

Если это учение — снести всё:

```bash
docker stop klemma-mi-restore-test
docker volume rm "$VOL"
rm -rf <каталог-с-расшифрованными-db>   # расшифрованные персданные — не оставлять на диске
```

Если это настоящее восстановление — остановить сломанный прод-контейнер,
переключить `docker-compose.yml` на новый том (`klemma-mi-data` вместо
битого), пересоздать через `docker compose up -d --force-recreate`, только
потом убрать `-p 127.0.0.1:18001` и вернуть Caddy на прод-порт.

## Известный предел

`.env` не в бэкапе намеренно (секреты не должны множиться по копиям). Значит
подстановка секретов из менеджера паролей вручную не автоматизирована и не
может быть: это единственная точка, требующая человека. Задокументированный
минимальный набор: `KLEMMA_JWT_SECRET`, `KLEMMA_BONUM_INGEST_TOKEN`,
`ANTHROPIC_API_KEY` (нужен на старте контейнера, хотя сам `/meetings/ingest`
его не использует).
