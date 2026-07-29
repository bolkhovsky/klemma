# Миграция Caddy на `sites.d` — одноразовая операция

Выполняется **один раз** на VPS `klemma` (82.146.38.192) перед первым деплоем
после этого изменения. До миграции деплой klemma не запускать: новый `Caddyfile`
содержит только `import`, и без разложенных файлов сайтов машина останется без
единого vhost.

## Зачем

`deploy.yml` копирует `saas/deploy/` целиком в `/opt/klemma/deploy/`, то есть
перезаписывает общий `Caddyfile` при каждом пуше в master. Пока все сайты жили в
этом файле, любой такой пуш стирал блоки соседних проектов — это уже происходило
с `rfm` и `miz` (коммит `b9ca8ac`), и ровно та же экспозиция была у блока Бонума.

После миграции каждый проект владеет своим файлом в `/opt/caddy/sites.d/`.
Каталог лежит **вне** `/opt/klemma/`, поэтому деплой klemma до него физически не
дотягивается.

## Что проверено заранее

- Caddy v2.11.4: `import` с glob, который ничего не нашёл, и отсутствующий
  каталог дают **warning, а не ошибку** — конфиг остаётся валидным.
  Обратная сторона: при неудачном mount сайты исчезнут молча, поэтому в
  `deploy.yml` добавлена проверка по реальному хосту с утверждением тела ответа.
- Сборка через `import` даёт JSON, эквивалентный монолитному конфигу: набор
  хостов и обработчики совпадают, отличается только порядок блоков (`import`
  грузит файлы по алфавиту) и служебный путь к исходному файлу. Хосты
  непересекающиеся, поэтому порядок на маршрутизацию не влияет — проверено
  сравнением `caddy adapt` обоих вариантов.

## Порядок

Все команды — на VPS под `deploy`.

### 1. Резервная копия

```bash
sudo cp /opt/klemma/deploy/Caddyfile /opt/klemma/deploy/Caddyfile.bak-pre-sitesd
```

### 2. Разложить текущие блоки по файлам

Границы блоков в текущем живом файле (132 строки):

| Строки | Файл в `/opt/caddy/sites.d/` | Владелец |
|---|---|---|
| 5–30 | `litresearch.ru.conf` | репозиторий klemma |
| 32–47 | `rfm.conf` | проект rfm-admin |
| 49–63 | `miz.conf` | проект miz-assist |
| 77–101 | `klemma-mi.conf` | контур MI (переедет в новый репозиторий) |
| 103–132 | `stt.conf` | контур MI |

```bash
sudo mkdir -p /opt/caddy/sites.d
C=/opt/klemma/deploy/Caddyfile
sudo sed -n '5,30p'    $C | sudo tee /opt/caddy/sites.d/litresearch.ru.conf >/dev/null
sudo sed -n '32,47p'   $C | sudo tee /opt/caddy/sites.d/rfm.conf            >/dev/null
sudo sed -n '49,63p'   $C | sudo tee /opt/caddy/sites.d/miz.conf            >/dev/null
sudo sed -n '77,101p'  $C | sudo tee /opt/caddy/sites.d/klemma-mi.conf      >/dev/null
sudo sed -n '103,132p' $C | sudo tee /opt/caddy/sites.d/stt.conf            >/dev/null
```

Блок Бонума (строки 64–76) — только комментарии, контур снят с публикации
28.07.2026. Не переносить.

**Сверить номера строк перед запуском** — файл мог измениться:

```bash
grep -n '^[a-z].*{$' /opt/klemma/deploy/Caddyfile
```

### 3. Заменить Caddyfile и добавить mount

```bash
cd /opt/klemma/deploy
# новый Caddyfile и docker-compose.yml приедут с деплоем; если делаете вручную —
# скопировать из репозитория saas/deploy/
sudo docker compose up -d --force-recreate caddy
```

Пересоздание контейнера, а не `reload`, обязательно: добавился новый bind mount
`/opt/caddy/sites.d`, и `reload` его не подхватит.

### 4. Проверка — до того, как расходиться

```bash
# конфиг валиден
sudo docker exec deploy-caddy-1 caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile

# все пять сайтов отвечают по HTTPS с осмысленным телом
curl -s https://litresearch.ru/api/health          # {"status":"ok",...}
curl -s https://klemma.bolkhovsky.ru/health        # {"status":"ok",...}
curl -s https://stt.bolkhovsky.ru/v1/health        # {"status":"ok","worker":{...}}
curl -s -o /dev/null -w '%{http_code}\n' https://rfm.bolkhovsky.ru/
curl -s -o /dev/null -w '%{http_code}\n' https://miz.bolkhovsky.ru/
```

Пустой ответ или ошибка TLS означает, что vhost не собрался — откат:

```bash
sudo cp /opt/klemma/deploy/Caddyfile.bak-pre-sitesd /opt/klemma/deploy/Caddyfile
sudo docker compose up -d --force-recreate caddy
```

## После миграции

- Владельцы `rfm` и `miz` должны завести свои `.conf` в своих репозиториях и
  класть их в `/opt/caddy/sites.d/` из своих деплоев. Сейчас эти два файла
  существуют только на сервере — тот же риск, что мы чиним, просто перенесённый.
- `klemma-mi.conf` и `stt.conf` версионируются в `saas/deploy/klemma-mi/sites.d/`
  и переедут в репозиторий портала.
- Порядок загрузки — алфавитный по имени файла. Пока хосты не пересекаются, это
  безразлично. Если появится wildcard-хост, порядок станет значимым: назвать файл
  так, чтобы он грузился последним.
