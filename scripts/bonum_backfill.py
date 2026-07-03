#!/usr/bin/env python3
"""One-shot backfill: Bitrix24 Disk protocols → Bonum portal (POST /meetings/ingest).

Pre-fills the prototype with ~6 months of real ОМС/Скрам protocol history instead
of a realtime Nodul webhook (deferred to after the case is closed). Walks the
"Mymeet - ИИ" folder tree on the shared Bitrix24 disk, downloads each
``Протокол_*.docx``, converts it to markdown via pandoc, and either prints it
(``--dry-run``) or POSTs it to the running portal's ingest endpoint. Idempotent:
``meeting_id`` is derived deterministically from the site folder name + protocol
date/time, so re-running never duplicates a meeting (``ingest_meeting`` replaces
fragments on re-ingest).

Requires:
    BONUM_BITRIX_WEBHOOK   Bitrix24 incoming webhook base URL (scope: disk).
                            NOT a CLI flag — keep it out of shell history/ps.
    pandoc                 on PATH (docx → gfm conversion).

Usage:
    # Inspect real protocol structure before touching the parser or prod:
    BONUM_BITRIX_WEBHOOK=https://bonum.bitrix24.ru/rest/USER/CODE \\
        python scripts/bonum_backfill.py --dry-run --limit 5 --save-md /tmp/samples

    # Full backfill into the live portal:
    BONUM_BITRIX_WEBHOOK=https://bonum.bitrix24.ru/rest/USER/CODE \\
        python scripts/bonum_backfill.py \\
        --url https://bonum-analytics.bolkhovsky.ru --token <ingest-token>
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

ROOT_FOLDER_NAME = "Mymeet - ИИ"
THROTTLE_SECONDS = 0.4
MAX_RETRIES = 4
MAX_WALK_DEPTH = 4

# "Протокол_<identifier>_DD-MM-YYYY_HH-MM.docx" (node_17_bitrix24_upload.js:205)
_FILENAME_RE = re.compile(
    r"^Протокол_.+_(\d{2})-(\d{2})-(\d{4})_(\d{2})-(\d{2})\.docx$", re.IGNORECASE
)


class BitrixError(RuntimeError):
    pass


# ── Bitrix24 REST client ───────────────────────────────────────────────────


def _bitrix_raw(webhook: str, method: str, params: Optional[dict] = None) -> dict:
    """POST one Bitrix24 REST call, retrying on 5xx / rate-limit errors."""
    url = f"{webhook.rstrip('/')}/{method}.json"
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=params or {}, timeout=30)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2**attempt)
            continue
        time.sleep(THROTTLE_SECONDS)
        if resp.status_code == 200:
            body = resp.json()
            if "error" not in body:
                return body
            if body["error"] == "QUERY_LIMIT_EXCEEDED" and attempt < MAX_RETRIES:
                time.sleep(2**attempt)
                continue
            raise BitrixError(f"{method}: {body['error']} — {body.get('error_description', '')}")
        if resp.status_code >= 500 and attempt < MAX_RETRIES:
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
    raise BitrixError(f"{method}: exhausted retries ({last_exc})")


def bitrix_call(webhook: str, method: str, params: Optional[dict] = None) -> Any:
    return _bitrix_raw(webhook, method, params).get("result")


def bitrix_list_all(webhook: str, method: str, params: Optional[dict] = None) -> list:
    """Paginate a Bitrix list method (fixed 50-record pages via ``start``)."""
    params = dict(params or {})
    items: list = []
    start = 0
    while True:
        params["start"] = start
        body = _bitrix_raw(webhook, method, params)
        items.extend(body.get("result") or [])
        nxt = body.get("next")
        if nxt is None:
            break
        start = nxt
    return items


def find_root_folder(webhook: str) -> int:
    """Locate the "Mymeet - ИИ" folder across all disk storages."""
    storages = bitrix_call(webhook, "disk.storage.getlist") or []
    for storage in storages:
        children = bitrix_list_all(webhook, "disk.storage.getchildren", {"id": storage["ID"]})
        for c in children:
            if c.get("TYPE") == "folder" and c.get("NAME") == ROOT_FOLDER_NAME:
                return int(c["ID"])
    raise BitrixError(f"Folder '{ROOT_FOLDER_NAME}' not found in any storage")


def list_site_folders(webhook: str, root_id: int, site_filter: Optional[str]) -> list[dict]:
    children = bitrix_list_all(webhook, "disk.folder.getchildren", {"id": root_id})
    folders = [c for c in children if c.get("TYPE") == "folder"]
    if site_filter:
        folders = [f for f in folders if site_filter.lower() in f["NAME"].lower()]
    return folders


def walk_files(webhook: str, folder_id: int, depth: int = 0) -> Iterator[dict]:
    """Recurse through a folder tree (site → month → day, per the upload node)."""
    children = bitrix_list_all(webhook, "disk.folder.getchildren", {"id": folder_id})
    for c in children:
        if c.get("TYPE") == "file":
            yield c
        elif c.get("TYPE") == "folder" and depth < MAX_WALK_DEPTH:
            yield from walk_files(webhook, int(c["ID"]), depth + 1)


def download_file(item: dict) -> bytes:
    url = item.get("DOWNLOAD_URL")
    if not url:
        raise BitrixError(f"No DOWNLOAD_URL for '{item.get('NAME')}'")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


# ── Filename / site parsing ─────────────────────────────────────────────────


def parse_protocol_filename(name: str) -> Optional[tuple[str, str]]:
    """Return ``(date, time)`` as ``("2026-06-24", "09:30")`` or ``None`` if the
    filename doesn't match the ``Протокол_*_DD-MM-YYYY_HH-MM.docx`` pattern."""
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    dd, mm, yyyy, hh, minute = m.groups()
    return f"{yyyy}-{mm}-{dd}", f"{hh}:{minute}"


def site_type_and_name(folder_name: str) -> tuple[str, str]:
    name = folder_name.strip()
    if name.upper().startswith("ОМС"):
        return "ОМС", name[3:].strip() or name
    if name.lower().startswith("скрам"):
        return "Скрам", name
    return "", name


def slugify(value: str) -> str:
    value = re.sub(r"[^\w]+", "-", value.strip().lower(), flags=re.UNICODE)
    return value.strip("-")[:40] or "site"


def build_meeting_id(site_folder_name: str, date_str: str, time_str: str) -> str:
    return f"{slugify(site_folder_name)}-{date_str.replace('-', '')}-{time_str.replace(':', '')}"


# ── docx → markdown ──────────────────────────────────────────────────────────


def docx_to_markdown(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".docx") as tmp:
        tmp.write(data)
        tmp.flush()
        result = subprocess.run(
            ["pandoc", "-f", "docx", "-t", "gfm", tmp.name],
            capture_output=True,
            text=True,
            check=True,
        )
    return result.stdout


# ── Ingest ────────────────────────────────────────────────────────────────


def ingest(url: str, token: str, payload: dict) -> dict:
    resp = requests.post(
        f"{url.rstrip('/')}/meetings/ingest",
        json=payload,
        headers={"X-Ingest-Token": token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="download+convert only, no ingest POST")
    ap.add_argument("--limit", type=int, default=0, help="stop after N protocols (0 = no limit)")
    ap.add_argument("--since", default="2026-01-01", help="skip protocols before this date (YYYY-MM-DD)")
    ap.add_argument("--site", default="", help="only process site folders matching this substring")
    ap.add_argument("--url", default="", help="portal base URL (required unless --dry-run)")
    ap.add_argument(
        "--token",
        default=os.environ.get("KLEMMA_BONUM_INGEST_TOKEN", ""),
        help="ingest token (default: $KLEMMA_BONUM_INGEST_TOKEN)",
    )
    ap.add_argument("--save-md", default="", help="directory to save converted markdown samples")
    args = ap.parse_args()

    webhook = os.environ.get("BONUM_BITRIX_WEBHOOK", "")
    if not webhook:
        ap.error("BONUM_BITRIX_WEBHOOK env var is required")
    if not args.dry_run and not args.url:
        ap.error("--url is required unless --dry-run")
    if not args.dry_run and not args.token:
        ap.error("--token (or $KLEMMA_BONUM_INGEST_TOKEN) is required unless --dry-run")

    save_dir = Path(args.save_md).expanduser() if args.save_md else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    print(f"→ resolving '{ROOT_FOLDER_NAME}' on {webhook.split('/rest/')[0]}...")
    root_id = find_root_folder(webhook)
    folders = list_site_folders(webhook, root_id, args.site or None)
    print(f"→ {len(folders)} site folder(s): {', '.join(f['NAME'] for f in folders)}")

    n_seen = n_skipped_pattern = n_skipped_date = n_processed = n_errors = 0
    n_fragments = n_embedded = 0
    skipped_names: list[str] = []

    for folder in folders:
        folder_files = list(walk_files(webhook, int(folder["ID"])))
        site_type, site_name = site_type_and_name(folder["NAME"])
        for item in folder_files:
            n_seen += 1
            parsed = parse_protocol_filename(item.get("NAME", ""))
            if not parsed:
                n_skipped_pattern += 1
                skipped_names.append(f"{folder['NAME']}/{item.get('NAME', '')}")
                continue
            date_str, time_str = parsed
            if date_str < args.since:
                n_skipped_date += 1
                continue

            meeting_id = build_meeting_id(folder["NAME"], date_str, time_str)
            try:
                data = download_file(item)
                md = docx_to_markdown(data)
            except Exception as e:
                n_errors += 1
                print(f"  ! {meeting_id}: {e}", file=sys.stderr)
                continue

            if save_dir:
                (save_dir / f"{meeting_id}.md").write_text(md, encoding="utf-8")

            payload = {
                "meeting_id": meeting_id,
                "date": date_str,
                "time": time_str,
                "type": site_type,
                "site": site_name,
                "protocol_md": md,
            }

            if args.dry_run:
                print(f"  [dry-run] {meeting_id}  ({len(md)} chars md)")
            else:
                try:
                    result = ingest(args.url, args.token, payload)
                except Exception as e:
                    n_errors += 1
                    print(f"  ! {meeting_id}: ingest failed — {e}", file=sys.stderr)
                    continue
                n_fragments += result.get("fragments", 0)
                n_embedded += result.get("embedded", 0)
                print(
                    f"  + {meeting_id}: {result.get('fragments', 0)} fragments, "
                    f"{result.get('embedded', 0)} embedded"
                )

            n_processed += 1
            if args.limit and n_processed >= args.limit:
                break
        if args.limit and n_processed >= args.limit:
            break

    print("\n" + "─" * 60)
    print(f"файлов просмотрено:      {n_seen}")
    print(f"не подошли под паттерн:  {n_skipped_pattern}")
    print(f"старше --since:          {n_skipped_date}")
    print(f"обработано протоколов:   {n_processed}")
    print(f"ошибок:                  {n_errors}")
    if not args.dry_run:
        print(f"фрагментов создано:      {n_fragments}")
        print(f"embedded:                {n_embedded}")
    if skipped_names:
        print("\nПропущенные файлы (не «Протокол_*.docx»), первые 20:")
        for name in skipped_names[:20]:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
