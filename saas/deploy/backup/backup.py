#!/usr/bin/env python3
"""Ежедневный бэкап SQLite-баз трёх контуров на VPS: klemma-mi (личный), bonum
(клиентский, контейнер остановлен) и klemma (litresearch).

Зачем свой скрипт, а не `sqlite3 .backup`: CLI `sqlite3` на VPS не установлен
(и его установку сознательно не тащим ради одной операции). Модуль
`sqlite3.Connection.backup()` в стандартной библиотеке Python — тот же online
backup API, семантика идентична, дополнительных пакетов не требует.

Снимаем изнутри контейнера, у которого база уже открыта (для klemma-mi и
litresearch — `docker exec` в работающий контейнер), а не сайдкаром с
read-only монтированием тома: WAL-режим для read-only доступа потребовал бы
создания `-shm`, которого может не быть. Для bonum (контейнер остановлен,
писателя нет) это ограничение не действует — используется одноразовый
контейнер на том же образе с обычным (не read-only) монтированием тома.

Запускается юнитом `klemma-backup.timer` (03:00 ежедневно). Ротация — отдельным
шагом в том же юните: `find /opt/backups -name "*.db" -mtime +7 -delete`.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from dataclasses import dataclass

BACKUP_DIR = "/opt/backups"

# Тот же инлайн-скрипт гоняется и внутри `docker exec` (klemma-mi, litresearch),
# и внутри одноразового `docker run` (bonum) — контейнеру нужен только stdlib.
_INNER_BACKUP_SCRIPT = (
    "import sqlite3,sys\n"
    "src,dst=sys.argv[1],sys.argv[2]\n"
    "conn=sqlite3.connect(src)\n"
    "try:\n"
    "    dest=sqlite3.connect(dst)\n"
    "    try:\n"
    "        conn.backup(dest)\n"
    "    finally:\n"
    "        dest.close()\n"
    "finally:\n"
    "    conn.close()\n"
)


@dataclass(frozen=True)
class Source:
    label: str
    container: str  # для bonum — образ, из которого поднимается одноразовый контейнер
    running: bool
    volume: str
    # (путь внутри контейнера/тома, имя файла в /opt/backups без даты-суффикса)
    dbs: tuple[tuple[str, str], ...]


SOURCES: tuple[Source, ...] = (
    Source(
        label="klemma-mi",
        container="klemma-mi-portal",
        running=True,
        volume="klemma-mi_klemma-mi-data",
        dbs=(
            ("/data/meetings/.klemma/data/klemma.db", "klemma-mi-klemma"),
            ("/data/saas/library.db", "klemma-mi-library"),
            ("/data/saas/project.db", "klemma-mi-project"),
            ("/data/saas/users.db", "klemma-mi-users"),
        ),
    ),
    Source(
        label="litresearch",
        container="deploy-api-1",
        running=True,
        volume="deploy_klemma-data",
        dbs=(
            ("/data/klemma/library.db", "litresearch-library"),
            ("/data/klemma/project.db", "litresearch-project"),
            ("/data/klemma/users.db", "litresearch-users"),
        ),
    ),
    Source(
        label="bonum",
        container="bonum-portal:latest",  # образ: контейнер остановлен, exec недоступен
        running=False,
        volume="deploy_bonum-data",
        dbs=(
            ("/data/meetings/.klemma/data/klemma.db", "bonum-klemma"),
            ("/data/saas/library.db", "bonum-library"),
            ("/data/saas/project.db", "bonum-project"),
            ("/data/saas/users.db", "bonum-users"),
        ),
    ),
)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def backup_running(source: Source, date_tag: str) -> list[str]:
    written = []
    for src_path, out_name in source.dbs:
        tmp_path = f"/tmp/{out_name}.backup.db"
        out_path = f"{BACKUP_DIR}/{out_name}-{date_tag}.db"
        _run(
            [
                "docker", "exec", source.container,
                "python3", "-c", _INNER_BACKUP_SCRIPT, src_path, tmp_path,
            ]
        )
        _run(["docker", "cp", f"{source.container}:{tmp_path}", out_path])
        _run(["docker", "exec", source.container, "rm", "-f", tmp_path])
        written.append(out_path)
    return written


def backup_stopped(source: Source, date_tag: str) -> list[str]:
    written = []
    for src_path, out_name in source.dbs:
        out_path = f"{BACKUP_DIR}/{out_name}-{date_tag}.db"
        tmp_in_container = f"/backup-out/{out_name}-{date_tag}.db"
        _run(
            [
                "docker", "run", "--rm",
                "-v", f"{source.volume}:/data",
                "-v", f"{BACKUP_DIR}:/backup-out",
                "--entrypoint", "python3",
                source.container,
                "-c", _INNER_BACKUP_SCRIPT, src_path, tmp_in_container,
            ]
        )
        written.append(out_path)
    return written


def main() -> int:
    date_tag = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    _run(["mkdir", "-p", BACKUP_DIR])

    all_written: list[str] = []
    failures: list[str] = []
    for source in SOURCES:
        try:
            fn = backup_running if source.running else backup_stopped
            all_written.extend(fn(source, date_tag))
        except subprocess.CalledProcessError as exc:
            failures.append(f"{source.label}: {exc.stderr.strip() if exc.stderr else exc}")

    for path in all_written:
        print(f"backed up: {path}")
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)

    # Ротация: старше 7 дней — вон. Отдельный process, не Python-логика,
    # чтобы `find` оставался единственным источником правды о возрасте файла.
    _run(["find", BACKUP_DIR, "-name", "*.db", "-mtime", "+7", "-delete"])

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
