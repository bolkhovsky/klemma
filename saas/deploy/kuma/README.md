# uptime-kuma — мониторинг, уровень 1 (C3)

Ловит отказ приложений (портал, воркер) — статусы, история, ключевые слова.
**Не ловит** смерть самого VPS, потери сети или отказа Caddy: во всех этих
случаях kuma умирает вместе с хостом и не отправляет ничего. Этот класс
отказов закрывает уровень 2 — независимая проба с fram
(`klemma-stt/deploy/fram/monitor-probe.py`), обязательная, не сокращаемая.

## Установка (вручную, один раз)

CI (`deploy.yml`) синхронизирует только `docker-compose.yml`/`sites.d` в
`/opt/klemma/deploy/`, сам контейнер не поднимает — как и `klemma-mi`, это
инфраструктурный шаг, а не часть основного стека.

```bash
ssh klemma
cd /opt/klemma/deploy/kuma
sudo docker compose up -d
```

`sites.d/status.conf` доезжает до `/opt/caddy/sites.d/` автоматически при
следующем деплое `klemma` (тем же путём, что и `litresearch.ru.conf`) — Caddy
подхватит его на ближайшем `reload`/пересоздании. Требует A-запись
`status.bolkhovsky.ru → 82.146.38.192` — без неё Caddy не получит TLS-сертификат.

## Настройка мониторов (в веб-интерфейсе, один раз)

Открыть `https://status.bolkhovsky.ru`, создать админ-аккаунт (первый вход),
затем:

1. **Settings → Status Pages** — не создавать ни одной публичной
   status-страницы. Дашборд раскрывает всю топологию контура (какие сайты
   есть, как называются мониторы) — наружу должна быть видна только форма
   входа.
2. **Add New Monitor** × 2, не просто «код ответа»: `/v1/health` у
   `stt.bolkhovsky.ru` отдаёт `200` даже при `degraded` — нужна проверка
   именно поля `status` в теле ответа.

   Тип монитора **HTTP(s) - Json Query** (найдено 01.08.2026: текстового
   `HTTP(s) - Keyword` в актуальной сборке нет — вместо него `Json Query`,
   который парсит поле через JSONata, а не ищет подстроку; точнее исходного
   плана, не хуже):

   | Поле | Монитор 1 | Монитор 2 |
   |---|---|---|
   | Friendly Name | klemma portal | stt worker |
   | URL | `https://klemma.bolkhovsky.ru/health` | `https://stt.bolkhovsky.ru/v1/health` |
   | Monitor Type | HTTP(s) - Json Query | HTTP(s) - Json Query |
   | Json Query | `status` | `status` |
   | Condition | `==` | `==` |
   | Expected Value | `ok` | `ok` |
   | Interval | 60s | 60s |
   | Retries | 2 | 2 |

   Если в вашей сборке всё же есть `HTTP(s) - Keyword` — тоже годится,
   Keyword-поле = `"status":"ok"` (с кавычками, ищет подстроку в сыром теле
   ответа; менее точно, чем Json Query, но эквивалентно по результату здесь).

3. **Settings → Notifications** — добавить Telegram (тот же бот, что и
   уровень 2 с fram, см. `monitor-probe.py`), привязать к обоим мониторам.
   Кнопка **Test** обязана дать сообщение в чат до того, как считать уровень 1
   настроенным.

## Почему рядом живёт `tg-relay`, а не прокси-переменные

`api.telegram.org` с этой машины напрямую не отвечает (01.08.2026: `curl` →
`000`), и первым решением были `HTTP_PROXY`/`HTTPS_PROXY` на локальный
tinyproxy. **Это не сработало**, и ошибка выглядела как чужая:

```
kuma:       Error: Request failed with status code 502
              at Telegram.send (server/notification-providers/telegram.js:31)
tinyproxy:  Request (fd 5): POST https://api.telegram.org/... HTTP/1.1
tinyproxy:  read_buffer: read() failed on fd 6: Connection reset by peer
```

Причина в axios: для `https`-цели он отправляет прокси **абсолютный URI**
обычным запросом вместо `CONNECT`-туннеля. Tinyproxy передаёт это апстриму,
тот рвёт соединение, kuma показывает 502. Адрес при этом захардкожен в
`telegram.js` — поля «Server URL» у провайдера в 1.23.17 нет, подменить
некуда.

Поэтому путь чинится на уровне сети, а не приложения: `tg-relay` (socat)
слушает `:443`, сам открывает `CONNECT`-туннель через tinyproxy и прозрачно
пробрасывает байты. TLS остаётся **сквозным до Telegram** — сертификат
настоящий, MITM нет, `telegram.js` не патчится. Перенаправление даёт
docker-DNS: контейнер поднят с сетевым алиасом `api.telegram.org`.

Проверка сквозного пути (ожидается ответ самого Telegram, а не прокси):

```bash
docker exec uptime-kuma node -e '
  require("/app/node_modules/axios")
    .get("https://api.telegram.org/bot0:invalid/getMe")
    .catch(e => console.log(e.response.status, JSON.stringify(e.response.data)))'
# → 401 {"ok":false,"error_code":401,"description":"Unauthorized: invalid token specified"}
```

`401` здесь — успех: значит запрос дошёл до Telegram и тот его разобрал. `502`
означает, что релей не поднят или у kuma снова появились прокси-переменные.

Побочный эффект, который надо знать: алиас действует на всю сеть
`deploy_default`, то есть любой соседний контейнер тоже пойдёт в Telegram
через релей. Это не регресс — напрямую оттуда всё равно не отвечает.

После правки `docker-compose.yml` контейнеры нужно пересоздать, а не
перезапустить:

```bash
sudo docker compose -f /opt/klemma/deploy/kuma/docker-compose.yml up -d --force-recreate
```

## Проверка (гейт C3)

«Монитор зелёный» ничего не доказывает — закрыт только после того, как
**остановленный сервис реально дал алерт**:

```bash
ssh fram 'sudo systemctl stop klemma-stt'   # ждать алерт в Telegram
ssh fram 'sudo systemctl start klemma-stt'  # ждать алерт о восстановлении
```
