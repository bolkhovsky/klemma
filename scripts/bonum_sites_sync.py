#!/usr/bin/env python3
"""Sync the Bonum portal sites registry from the Nodul webhook + remap meetings.

Fetches the sites collection from the webhook (``--url`` or env
``KLEMMA_BONUM_SITES_WEBHOOK``), upserts ``portal_sites`` in the meeting DB at
``<root>/.klemma/data/klemma.db``, then re-resolves ``site_slug`` for EVERY
meeting and prints the full distribution + the list of unmapped meetings —
silent mis-mapping must be impossible (verbose-mutations principle).

``--dry-run`` fetches and resolves in memory, printing the would-be result
WITHOUT writing anything.

Usage:
    KLEMMA_BONUM_SITES_WEBHOOK=https://... \
        python scripts/bonum_sites_sync.py --root ~/klemma-bonum-demo
    python scripts/bonum_sites_sync.py --root ~/klemma-bonum-demo \
        --url https://... --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from klemma.meetings import BONUM_ROOT_ENV, _meeting_meta_map, bonum_db_path  # noqa: E402
from klemma.meetings_sites import (  # noqa: E402
    EXPLICIT,
    ensure_portal_tables,
    parse_sites_webhook,
    pick_site_slug,
    remap_meeting_sites,
    upsert_sites,
)
from klemma.state import StateManager  # noqa: E402


def fetch_sites(url: str) -> list[dict]:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    items = parse_sites_webhook(resp.json())
    if not items:
        raise SystemExit("✗ webhook returned no site entries — aborting")
    return items


def print_report(
    distribution: dict[str, int],
    unmapped: list[tuple[str, str, str]],
    names: dict[str, str],
) -> None:
    print("\nРаспределение совещаний по площадкам:")
    print(f"  {'slug':<36} {'название':<40} {'совещаний':>9}")
    for slug, count in sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0])):
        label = names.get(slug, "") if slug else "(не сопоставлено)"
        print(f"  {slug or '—':<36} {label:<40} {count:>9}")
    if unmapped:
        print(f"\nНесопоставленные совещания ({len(unmapped)}):")
        for date_str, site, title in sorted(unmapped):
            print(f"  [{date_str or '????-??-??'}] site='{site}' title='{title}'")
    else:
        print("\nВсе совещания сопоставлены с площадками.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync portal sites registry + remap meetings")
    ap.add_argument(
        "--root",
        default=os.environ.get(BONUM_ROOT_ENV),
        help=f"Meeting project root (default: ${BONUM_ROOT_ENV})",
    )
    ap.add_argument(
        "--url",
        default=os.environ.get("KLEMMA_BONUM_SITES_WEBHOOK", ""),
        help="Sites webhook URL (default: $KLEMMA_BONUM_SITES_WEBHOOK)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + resolve + print without writing to the DB",
    )
    args = ap.parse_args()

    if not args.root:
        ap.error(f"--root is required (or set {BONUM_ROOT_ENV})")
    if not args.url:
        ap.error("--url is required (or set KLEMMA_BONUM_SITES_WEBHOOK)")

    db = bonum_db_path(args.root)
    if not db.exists():
        raise SystemExit(f"✗ meeting DB not found at {db}")
    state = StateManager(str(db))

    print(f"→ Fetching sites from webhook … ({'dry-run' if args.dry_run else 'live'})")
    items = fetch_sites(args.url)
    enabled = [i for i in items if i.get("enabled", True)]
    print(f"  {len(items)} sites received ({len(enabled)} enabled):")
    for i in items:
        flag = "✓" if i.get("enabled", True) else "✗"
        print(f"  {flag} {i.get('site_slug', ''):<36} {i.get('site_name', '')}")

    names = {str(i.get("site_slug") or ""): str(i.get("site_name") or "") for i in items}
    metas = _meeting_meta_map(state)

    if args.dry_run:
        # Resolve in memory against the fetched (not stored) registry.
        distribution: dict[str, int] = {}
        unmapped: list[tuple[str, str, str]] = []
        for meta in metas.values():
            site = str(meta.get("site") or "")
            title = str(meta.get("title") or "")
            # Тем же правилом, что и живой прогон (pick_site_slug), иначе dry-run
            # обещал бы пересчёт площадок, которые remap на самом деле сохранит.
            slug, _source = pick_site_slug(
                str(meta.get("site_slug") or "") if meta.get("site_slug_source") == EXPLICIT else "",
                site,
                title,
                enabled,
            )
            distribution[slug] = distribution.get(slug, 0) + 1
            if not slug:
                unmapped.append((str(meta.get("date") or ""), site, title))
        print_report(distribution, unmapped, names)
        print("\n(dry-run: DB not modified)")
        return

    ensure_portal_tables(state)
    n = upsert_sites(state, items)
    result = remap_meeting_sites(state)
    print(f"\n→ Upserted {n} sites; remapped {result['mapped'] + result['unmapped']} meetings "
          f"({result['mapped']} mapped, {result['unmapped']} unmapped, "
          f"{result['preserved']} kept as sender-supplied)")

    unmapped = [
        (str(m.get("date") or ""), str(m.get("site") or ""), str(m.get("title") or ""))
        for m in _meeting_meta_map(state).values()
        if not m.get("site_slug")
    ]
    print_report(result["distribution"], unmapped, names)


if __name__ == "__main__":
    main()
