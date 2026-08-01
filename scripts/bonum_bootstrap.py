#!/usr/bin/env python3
"""Bootstrap real Bonum portal accounts + project (production, no synthetic data).

Unlike seed_bonum_demo.py (local demo with synthetic protocols), this only
creates user accounts and the "Бонум" project in users.db — the meeting DB stays
empty and is filled continuously by the Nodul webhook (POST /meetings/ingest).

Run inside the bonum container:
    docker compose -f /opt/bonum/docker-compose.yml exec bonum-portal \
        python scripts/bonum_bootstrap.py \
        --user "nadezhda@bonum.ru:<pwd>:Надежда Михайленко" \
        --user "ilya@bonum.ru:<pwd>:Илья Болховский"

``--site`` seeds the sites registry by hand, for contours where the Nodul sites
webhook is not reachable (local test rigs). In production the registry comes
from ``POST /meetings/sites/sync`` and hand-seeded rows are overwritten by it.

KLEMMA_DATA_DIR must match the running app (users.db location).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _parse_access_spec(ap: argparse.ArgumentParser, spec: str) -> tuple[str, str, list[str]]:
    """Parse ``email:director`` or ``email:leader:slug1,slug2`` → (email, role, slugs)."""
    parts = spec.split(":")
    email = parts[0].strip()
    role = parts[1].strip() if len(parts) > 1 else ""
    slugs = (
        [s.strip() for s in parts[2].split(",") if s.strip()] if len(parts) > 2 else []
    )
    if not email or role not in ("director", "leader"):
        ap.error(f"bad --access '{spec}', expected EMAIL:director or EMAIL:leader:slug1,slug2")
    if role == "leader" and not slugs:
        ap.error(f"bad --access '{spec}': leader needs at least one site slug")
    return email, role, slugs


def _parse_site_spec(ap: argparse.ArgumentParser, spec: str) -> dict:
    """Parse ``slug:name[:type[:leader]]`` → an upsert_sites item dict."""
    parts = spec.split(":")
    slug = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 else ""
    if not slug or not name:
        ap.error(f"bad --site '{spec}', expected SLUG:NAME[:TYPE[:LEADER]]")
    return {
        "site_slug": slug,
        "site_name": name,
        "site_type": (parts[2].strip() if len(parts) > 2 and parts[2].strip() else "oms"),
        "leader": (parts[3].strip() if len(parts) > 3 else ""),
        # Keywords drive the fuzzy resolver for payloads without an explicit
        # slug; the name itself is the sane default seed.
        "site_keywords": [name.lower()],
        "enabled": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Bootstrap Bonum portal accounts + project")
    ap.add_argument(
        "--user",
        action="append",
        default=[],
        metavar="EMAIL:PASSWORD:NAME",
        help="Account to create (repeatable). NAME may contain spaces.",
    )
    ap.add_argument(
        "--access",
        action="append",
        default=[],
        metavar="EMAIL:ROLE[:SLUGS]",
        help="Portal access row (repeatable): 'email:director' or "
        "'email:leader:slug1,slug2'. User must already exist (or be created "
        "via --user in the same run).",
    )
    ap.add_argument(
        "--site",
        action="append",
        default=[],
        metavar="SLUG:NAME[:TYPE[:LEADER]]",
        help="Sites-registry row (repeatable): 'oms_test:ОМС Тест' or "
        "'oms_test:ОМС Тест:oms:Иванов И.И.'. Needed where the Nodul webhook "
        "is unreachable — uploads are rejected for slugs absent from the registry.",
    )
    ap.add_argument("--project-name", default="Бонум")
    ap.add_argument(
        "--data-dir",
        default=os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")),
        help="users.db location (default: $KLEMMA_DATA_DIR or ~/.klemma)",
    )
    ap.add_argument(
        "--root",
        default=os.environ.get("KLEMMA_BONUM_PROJECT_ROOT"),
        help="Meeting project root — required for --access "
        "(default: $KLEMMA_BONUM_PROJECT_ROOT)",
    )
    ap.add_argument("--tokens", type=int, default=1_000_000, help="AI token grant per user")
    args = ap.parse_args()

    if not args.user and not args.access and not args.site:
        ap.error("at least one --user EMAIL:PASSWORD:NAME (or --access/--site) is required")
    if args.access and not args.root:
        ap.error("--access requires --root (or KLEMMA_BONUM_PROJECT_ROOT) for the meeting DB")
    if args.site and not args.root:
        ap.error("--site requires --root (or KLEMMA_BONUM_PROJECT_ROOT) for the meeting DB")

    from klemma.api.auth.password import hash_password
    from klemma.stores.user_store import LocalUserStore

    data_dir = Path(args.data_dir).expanduser()
    store = LocalUserStore(data_dir / "users.db")
    print(f"→ users.db at {data_dir}/users.db")

    owner_id = None
    for spec in args.user:
        parts = spec.split(":", 2)
        if len(parts) < 2:
            ap.error(f"bad --user '{spec}', expected EMAIL:PASSWORD[:NAME]")
        email, password = parts[0].strip(), parts[1]
        name = parts[2].strip() if len(parts) == 3 else ""
        user = store.get_user_by_email(email)
        if user is None:
            user = store.create_user(
                email=email, password_hash=hash_password(password), name=name
            )
            store.grant_tokens(user.user_id, args.tokens)
            print(f"  + created {email} ({name})")
        else:
            print(f"  = exists  {email}")
        if owner_id is None:
            owner_id = user.user_id

    # One shared "Бонум" project owned by the first user
    proj = None
    if owner_id is not None:
        projects = []
        try:
            projects = store.get_projects(owner_id)
        except Exception:
            projects = []
        proj = next((p for p in projects if p.get("name") == args.project_name), None)
        if proj is None:
            proj = store.create_project(owner_id, args.project_name, "dissertation")
            print(f"  + created project '{args.project_name}'")
        else:
            print(f"  = project '{args.project_name}' exists")

    # Sites registry lives in the MEETING DB (portal_sites). Seeded before the
    # access rows below, so a leader's --access slugs point at existing sites.
    if args.site:
        from klemma.meetings import bonum_db_path
        from klemma.meetings_sites import upsert_sites
        from klemma.state import StateManager

        site_state = StateManager(str(bonum_db_path(args.root)))
        items = [_parse_site_spec(ap, spec) for spec in args.site]
        n = upsert_sites(site_state, items)
        for item in items:
            print(f"  ✓ site {item['site_slug']}: {item['site_name']}")
        print(f"  → {n} site row(s) in the registry")

    # Portal access rows live in the MEETING DB (portal_access), keyed by the
    # users.db user_id — look each account up by email.
    if args.access:
        from klemma.meetings import bonum_db_path
        from klemma.meetings_sites import ensure_portal_tables, set_access
        from klemma.state import StateManager

        meeting_state = StateManager(str(bonum_db_path(args.root)))
        ensure_portal_tables(meeting_state)
        for spec in args.access:
            email, role, slugs = _parse_access_spec(ap, spec)
            u = store.get_user_by_email(email)
            if u is None:
                print(f"  ⚠ access skipped — no such user: {email}")
                continue
            set_access(meeting_state, u.user_id, role, slugs)
            suffix = f" → {', '.join(slugs)}" if slugs else ""
            print(f"  ✓ access {email}: {role}{suffix}")

    print("\n" + "─" * 60)
    if proj is not None:
        print(f"project_id: {proj['project_id']}")
        print(f"portal:     /{proj['project_id']}/portal/meetings")


if __name__ == "__main__":
    main()
