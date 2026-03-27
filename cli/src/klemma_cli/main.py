"""klemma-cli — API-native sync client for Klemma SaaS.

Commands: link, push, pull, status, login

Sync is via REST API only. Local git operations (commit, log) are available
directly through git or via gitops helpers for those who prefer the CLI.
"""

from __future__ import annotations

from datetime import datetime, timezone

import click

from . import __version__


@click.group()
@click.version_option(version=__version__, prog_name="klemma-cli")
def cli() -> None:
    """Klemma CLI — sync your research project with litresearch.ru."""


@cli.command()
@click.option("--api-url", default="https://litresearch.ru/api", help="API base URL")
@click.option("--email", default=None, help="Account email")
@click.option("--password", default=None, help="Account password")
def link(api_url: str, email: str | None, password: str | None) -> None:
    """Connect this project to Klemma SaaS.

    Discovers .klemma/ project root, logs in, finds or creates a dashboard project,
    and saves the sync configuration.
    """
    from .auth import login
    from .client import KlemmaClient
    from .gitops import write_gitignore
    from .models import SyncConfig
    from .project import ensure_project_root, get_project_name
    from .state import save_sync_config

    # 0. Show server URL and project BEFORE asking for credentials
    click.echo(f"Server: {api_url}")
    project_root = ensure_project_root()
    project_name = get_project_name(project_root)
    click.echo(f"Project: {project_name} ({project_root})")
    click.echo()

    # 1. Prompt for credentials
    if not email:
        email = click.prompt("Email")
    if not password:
        password = click.prompt("Password", hide_input=True)

    # 2. Login
    click.echo("Logging in...")
    try:
        auth_data = login(api_url, email, password)
    except Exception as e:
        click.echo(f"Login failed: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"Logged in as {auth_data['email']}")

    # 3. Find or create dashboard project
    client = KlemmaClient(api_url=api_url, access_token=auth_data["access_token"])
    dashboard_project_id = ""
    try:
        resp = client.get("/projects")
        projects = resp.json().get("projects", [])
        # Find existing project by name (case-insensitive)
        match = next(
            (p for p in projects if p["name"].lower() == project_name.lower()),
            None,
        )
        if match:
            dashboard_project_id = match["project_id"]
            click.echo(f"Found existing project: {project_name} ({dashboard_project_id[:8]})")
        else:
            create_resp = client.post("/projects", json={"name": project_name})
            dashboard_project_id = create_resp.json()["project_id"]
            click.echo(f"Created project: {project_name} ({dashboard_project_id[:8]})")
    except Exception as e:
        click.echo(
            f"Warning: could not find/create dashboard project ({e}).\n"
            "Draft sync will be skipped. Re-run 'klemma-cli link' to retry.",
            err=True,
        )

    # 4. Write .gitignore (local git stays, just no server transport)
    write_gitignore(project_root)

    # 5. Save sync config
    config = SyncConfig(
        api_url=api_url,
        dashboard_project_id=dashboard_project_id,
    )
    save_sync_config(project_root, config)
    click.echo("Linked! Run 'klemma-cli push' to sync.")


@cli.command()
@click.option("--api-url", default=None, help="API base URL (defaults to saved URL)")
@click.option("--email", default=None, help="Account email")
@click.option("--password", default=None, help="Account password")
def login(api_url: str | None, email: str | None, password: str | None) -> None:
    """Refresh auth tokens without changing sync config.

    Use when 'klemma-cli pull' or 'push' returns 401 Unauthorized.
    """
    from .auth import load_auth
    from .auth import login as do_login

    existing = load_auth()
    resolved_url = api_url or (existing["api_url"] if existing else "https://litresearch.ru/api")
    resolved_email = email or (existing.get("email", "") if existing else "")

    if not resolved_email:
        resolved_email = click.prompt("Email")
    if not password:
        password = click.prompt("Password", hide_input=True)

    try:
        auth_data = do_login(resolved_url, resolved_email, password)
        click.echo(f"Logged in as {auth_data['email']}. Tokens saved to ~/.klemma-cli/auth.json")
    except Exception as e:
        click.echo(f"Login failed: {e}", err=True)
        raise SystemExit(1)


@cli.command()
def push() -> None:
    """Push local changes to Klemma SaaS (library + drafts via API)."""
    from .client import KlemmaClient
    from .project import ensure_project_root
    from .state import load_sync_config, save_sync_config
    from .sync import push_drafts, push_library

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    client = KlemmaClient(api_url=config.api_url)

    # Phase 1: Library
    click.echo("Pushing library data...")
    library_ok = False
    try:
        result = push_library(client, project_root)
        click.echo(
            f"Pushed {result['sources']} sources, "
            f"{result['fragments']} fragments, "
            f"{result['embeddings']} embeddings"
        )
        library_ok = True
    except Exception as exc:
        click.echo(f"Library push failed: {exc}", err=True)

    # Phase 2: Drafts
    drafts_ok = False
    if config.dashboard_project_id:
        click.echo("Pushing draft files...")
        try:
            draft_result = push_drafts(client, project_root, config.dashboard_project_id)
            if draft_result["files"]:
                click.echo(
                    f"Pushed {draft_result['files']} draft file(s), "
                    f"{draft_result['words']} words"
                )
            else:
                click.echo("No draft files found (draft/ is empty or missing).")
            drafts_ok = True
        except Exception as exc:
            click.echo(f"Draft push failed: {exc}", err=True)
    else:
        click.echo("Draft push skipped (no dashboard_project_id — re-run 'klemma-cli link').")
        drafts_ok = True  # N/A, not a failure

    if library_ok and drafts_ok:
        config.last_push = datetime.now(timezone.utc).isoformat()
    else:
        click.echo("Warning: last_push not updated due to failed phase(s).", err=True)
    save_sync_config(project_root, config)


@cli.command()
def pull() -> None:
    """Pull changes from Klemma SaaS (library + drafts via API)."""
    from .client import KlemmaClient
    from .project import ensure_project_root
    from .state import load_sync_config, save_sync_config
    from .sync import pull_drafts, pull_library

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    client = KlemmaClient(api_url=config.api_url)

    # Phase 1: Library
    click.echo("Pulling library data...")
    try:
        result = pull_library(client, project_root, since=config.last_pull or None)
        click.echo(
            f"Pulled {result['sources']} sources, "
            f"{result['fragments']} fragments"
        )
        config.last_pull = datetime.now(timezone.utc).isoformat()
        save_sync_config(project_root, config)
    except Exception as exc:
        click.echo(f"Library pull failed: {exc}", err=True)

    # Phase 2: Drafts
    if config.dashboard_project_id:
        click.echo("Pulling draft files...")
        try:
            draft_result = pull_drafts(client, project_root, config.dashboard_project_id)
            if draft_result["files"]:
                click.echo(f"Updated {draft_result['files']} draft file(s)")
            else:
                click.echo("Draft files up to date.")
        except Exception as exc:
            click.echo(f"Draft pull failed: {exc}", err=True)
    else:
        click.echo("Draft pull skipped (no dashboard_project_id — re-run 'klemma-cli link').")


@cli.command("status")
def sync_status() -> None:
    """Show sync status — local changes + server library counts."""
    from .client import KlemmaClient
    from .gitops import status as git_status
    from .project import ensure_project_root
    from .state import load_sync_config

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    # Local file changes
    local = git_status(project_root)
    click.echo("=== Local changes ===")
    click.echo(local or "(clean)")

    # Library status from server
    click.echo("\n=== Library ===")
    try:
        client = KlemmaClient(api_url=config.api_url)
        resp = client.get(f"/sync/status/{config.dashboard_project_id}")
        data = resp.json()
        click.echo(f"  Sources: {data['source_count']}")
        click.echo(f"  Fragments: {data['fragment_count']}")
    except Exception as e:
        click.echo(f"  (could not reach server: {e})")


if __name__ == "__main__":
    cli()
