# Бэкапы (C1)

Ежедневный снимок SQLite-баз трёх контуров на VPS — `klemma-mi` (личный
стенд), `litresearch` (`deploy-api-1`), `bonum` (клиентский, контейнер
остановлен с 28.07, том живой). `sqlite3` CLI на VPS не установлен —
`backup.py` использует `sqlite3.Connection.backup()` из стандартной
библиотеки Python через `docker exec`/`docker run`, подробности в докстринге
скрипта.

Ретенция — 7 дней, `find /opt/backups -name "*.db" -mtime +7 -delete`, шаг
внутри того же `backup.py`.

Установка не автоматизирована через `deploy.yml` (это инфраструктурный, а не
прикладной шаг — CI трогает только контейнеры). Один раз вручную на VPS,
после того как `saas/deploy/` синхронизирован в `/opt/klemma/deploy/`
(обычный `deploy.yml` уже это делает при каждом пуше в master):

```bash
ssh klemma
sudo install -m 644 /opt/klemma/deploy/backup/klemma-backup.service /etc/systemd/system/
sudo install -m 644 /opt/klemma/deploy/backup/klemma-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now klemma-backup.timer
```

Проверка: `sudo systemctl start klemma-backup.service && journalctl -u
klemma-backup.service -n 30` — должно появиться 11 строк `backed up: ...` (4
базы klemma-mi + 3 litresearch + 4 bonum) и свежие файлы в `/opt/backups/`.

## Офф-машинная копия

Уходит на fram шифрованной (`age`) — см. `klemma-stt` репозиторий,
`deploy/fram/backup-pull.py` и `deploy/fram/README.md`. Приватный ключ
расшифровки хранится в менеджере паролей, не на fram и не в репозитории;
публичный ключ — `deploy/fram/backup-age-recipient.txt` в `klemma-stt`.

## Восстановление

См. `RESTORE.md` рядом.
