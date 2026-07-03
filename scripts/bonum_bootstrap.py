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

KLEMMA_DATA_DIR must match the running app (users.db location).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Bootstrap Bonum portal accounts + project")
    ap.add_argument(
        "--user",
        action="append",
        default=[],
        metavar="EMAIL:PASSWORD:NAME",
        help="Account to create (repeatable). NAME may contain spaces.",
    )
    ap.add_argument("--project-name", default="Бонум")
    ap.add_argument(
        "--data-dir",
        default=os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma")),
        help="users.db location (default: $KLEMMA_DATA_DIR or ~/.klemma)",
    )
    ap.add_argument("--tokens", type=int, default=1_000_000, help="AI token grant per user")
    args = ap.parse_args()

    if not args.user:
        ap.error("at least one --user EMAIL:PASSWORD:NAME is required")

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

    print("\n" + "─" * 60)
    print(f"project_id: {proj['project_id']}")
    print(f"portal:     /{proj['project_id']}/portal/meetings")


if __name__ == "__main__":
    main()
