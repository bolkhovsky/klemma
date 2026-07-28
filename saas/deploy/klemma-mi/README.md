# Личный контур Meeting Intelligence — `klemma.bolkhovsky.ru`

Витрина, которую показывают лидам. Не путать с клиентским контуром Бонума
(`bonum-analytics.bolkhovsky.ru`), снятым с публикации 28.07.2026.

## Состав

| Что | Где |
|---|---|
| Портал (этот контур) | VPS `klemma`, `/opt/klemma-mi/`, контейнер `klemma-mi-portal` |
| STT-воркер | домашний сервер `fram`, публикуется как `stt.bolkhovsky.ru` через обратный SSH-туннель |
| Мобильный клиент | репозиторий `klemma-mobile`, APK раздаётся с `klemma.bolkhovsky.ru/apk/<токен>/` |
| vhost'ы Caddy | `sites.d/klemma-mi.conf`, `sites.d/stt.conf` → `/opt/caddy/sites.d/` |

## Развёрнутая версия

Записана в `DEPLOYED_SHA`. Обновлять при каждой пересборке образа — иначе
восстановить контур после потери можно будет только по памяти, как было до
29.07.2026.

Если `DEPLOYED_SHA` разошёлся с реальностью, версию можно определить обратно:
сравнить md5 файлов в `/opt/klemma-mi/build/repo/src/klemma/` с содержимым
коммитов (`git show <sha>:<путь>`). Каталог `build/repo/` — rsync-срез без
git-метаданных, собственной версии он не хранит.

## Сборка и деплой — вручную

Автоматического воркфлоу для этого контура нет и не будет: он переезжает в
отдельный коммерческий репозиторий, где деплой собирается сразу правильно —
с тегом `:previous`, снапшотом БД и откатом. До переезда порядок такой:

```bash
# на Маке: собрать SPA в режиме портала и синхронизировать срез
cd saas/dashboard && VITE_API_BASE='' VITE_PORTAL_ONLY=1 npm run build

rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '__pycache__' \
  src prompts scripts saas pyproject.toml README.md \
  klemma:/opt/klemma-mi/build/repo/

# на VPS
ssh klemma
cd /opt/klemma-mi
# ЗАФИКСИРОВАТЬ версию, из которой собираем
git -C ~/klemma-src rev-parse HEAD > /opt/klemma-mi/DEPLOYED_SHA   # или вписать вручную
sudo docker image inspect klemma-mi-portal:latest >/dev/null 2>&1 && \
  sudo docker tag klemma-mi-portal:latest klemma-mi-portal:previous
sudo docker build -t klemma-mi-portal:latest -f build/repo/saas/deploy/bonum/Dockerfile build/repo
sudo docker compose up -d --force-recreate
```

Откат:

```bash
sudo docker tag klemma-mi-portal:previous klemma-mi-portal:latest
sudo docker compose up -d --force-recreate
```

## Смоук после любого изменения

```bash
curl -s https://klemma.bolkhovsky.ru/health     # {"status":"ok",...}
curl -s https://stt.bolkhovsky.ru/v1/health     # {"status":"ok","worker":{"running":true,...}}
```

Полный предпоказный чеклист — `saas/deploy/SMOKE.md` (блок C4).

## Известные связки, которые надо развязать при переезде

- Раздача APK берёт каталог `/srv/dashboard/apk`, а этот mount принадлежит
  compose-проекту научной Клеммы (`saas/deploy/docker-compose.yml`, сервис
  `caddy`). Коммерческий контур не должен зависеть от научного.
- Имена переменных всё ещё `KLEMMA_BONUM_*`, хотя контур личный и Бонум к нему
  отношения не имеет. Переименовать в `KLEMMA_MEETINGS_*` при переезде.
- Образ собирается Dockerfile'ом из `saas/deploy/bonum/` — тоже наследие.
