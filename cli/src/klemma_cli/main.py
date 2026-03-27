"""klemma-cli — git-native sync client for Klemma SaaS.

Commands: link, push, pull, status, rollback
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
@click.option("--api-url", default="https://litresearch.ru/api", help="API base URL (include /api for production)")
@click.option("--email", default=None, help="Account email")
@click.option("--password", default=None, help="Account password")
def link(api_url: str, email: str | None, password: str | None) -> None:
    """Connect this project to Klemma SaaS.

    Discovers .klemma/ project root, logs in, creates a server-side git repo,
    initializes local git, and sets up the 'klemma' remote.
    """
    from .auth import login
    from .client import KlemmaClient
    from .gitops import add_files, add_remote, commit, init, is_git_repo, write_gitignore
    from .models import SyncConfig
    from .project import ensure_project_root, get_project_name
    from .state import save_sync_config

    # 0. Show server URL and project BEFORE asking for credentials
    click.echo(f"Server: {api_url}")
    project_root = ensure_project_root()
    project_name = get_project_name(project_root)
    click.echo(f"Project: {project_name} ({project_root})")
    click.echo()

    # 1. Prompt for credentials (after user sees where they're going)
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
    username = auth_data.get("username", "")
    click.echo(f"Logged in as {auth_data['email']} ({username})")

    # 3. Create server-side repo (username/project-name format, like GitHub)
    client = KlemmaClient(api_url=api_url, access_token=auth_data["access_token"])
    project_slug = project_name.lower().replace(" ", "-").replace("_", "-")

    try:
        resp = client.post("/sync/init-repo", json={"project_id": project_slug})
        repo_data = resp.json()
    except Exception as e:
        error_msg = str(e)
        if "409" in error_msg:
            click.echo("Server repo already exists — reconnecting.")
            base = api_url.rstrip("/").removesuffix("/api")
            namespaced = f"{username}/{project_slug}" if username else project_slug
            repo_data = {"git_url": f"{base}/git/{namespaced}.git", "access_token": ""}
            # Look up dashboard_project_id for this git project (#260 item 5)
            try:
                lookup = client.get("/sync/dashboard-project", params={"project_id": namespaced})
                lookup.raise_for_status()
                repo_data["dashboard_project_id"] = lookup.json().get("dashboard_project_id", "")
            except Exception as lookup_err:
                click.echo(
                    f"Warning: could not resolve dashboard project ID ({lookup_err}).\n"
                    "Draft sync will be skipped until this is resolved.\n"
                    "Open https://litresearch.ru, create a project, then re-run 'klemma-cli link'.",
                    err=True,
                )
        else:
            click.echo(f"Failed to create server repo: {e}", err=True)
            raise SystemExit(1)

    git_url = repo_data["git_url"]
    access_token = repo_data.get("access_token", "")

    # 4. Init local git
    if not is_git_repo(project_root):
        click.echo("Initializing git repository...")
        init(project_root)

    # 5. Add remote
    if access_token:
        # Embed token in URL for MVP (Option A)
        proto, rest = git_url.split("://", 1)
        authed_url = f"{proto}://token:{access_token}@{rest}"
    else:
        authed_url = git_url

    add_remote(project_root, "klemma", authed_url)
    click.echo(f"Remote 'klemma' set to {git_url}")

    # 6. Auto-generate .gitignore
    write_gitignore(project_root)

    # 7. Initial commit + push
    add_files(project_root, ["KLEMMA.md", "draft/", "notes/", ".gitignore"])
    commit_hash = commit(project_root, f"sync: initial link — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    if commit_hash:
        click.echo(f"Initial commit: {commit_hash[:8]}")

    # 8. Save sync config
    config = SyncConfig(
        project_id=repo_data.get("project_id", project_slug),
        api_url=api_url,
        git_url=git_url,
        access_token=access_token,
        dashboard_project_id=repo_data.get("dashboard_project_id", ""),
    )
    save_sync_config(project_root, config)
    click.echo("Linked! Run 'klemma-cli push' to sync.")


@cli.command()
def push() -> None:
    """Push local changes to Klemma SaaS.

    Two phases: git push (files) + API push (library data).
    """
    from .client import KlemmaClient
    from .gitops import add_files, commit
    from .gitops import push as git_push
    from .project import ensure_project_root
    from .state import load_sync_config, save_sync_config
    from .sync import push_drafts, push_library

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    # Phase 1: Git (may fail if git-http-backend not configured on server)
    click.echo("Pushing files...")
    add_files(project_root, [
        "KLEMMA.md",
        "draft/",
        "notes/research/",
        ".gitignore",
    ])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    commit_hash = commit(project_root, f"sync: push from CLI — {now}")
    if commit_hash:
        click.echo(f"Committed: {commit_hash[:8]}")
    else:
        click.echo("No file changes to commit.")

    try:
        success = git_push(project_root)
        if not success:
            click.echo("Push rejected — run 'klemma-cli pull' first.", err=True)
        else:
            click.echo("Files pushed.")
    except Exception as exc:
        click.echo(f"Git push failed: {exc}", err=True)

    # Phase 2: Library
    click.echo("Pushing library data...")
    client = KlemmaClient(api_url=config.api_url)
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

    # Phase 3: Drafts
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
                click.echo("No draft files found (draft/ is empty or missing — run migrate_drafts.py).")
            drafts_ok = True
        except Exception as exc:
            click.echo(f"Draft push failed: {exc}", err=True)
    else:
        click.echo("Draft push skipped (no dashboard_project_id — re-run 'klemma-cli link').")
        drafts_ok = True  # N/A, not a failure

    # Only update last_push when library + drafts both succeeded (#260 item 3)
    if library_ok and drafts_ok:
        config.last_push = datetime.now(timezone.utc).isoformat()
    else:
        click.echo("Warning: last_push not updated due to failed phase(s).", err=True)
    save_sync_config(project_root, config)


@cli.command()
def pull() -> None:
    """Pull changes from Klemma SaaS.

    Three phases: git pull (files) + API pull (library data) + draft files.
    """
    from .client import KlemmaClient
    from .gitops import pull as git_pull
    from .project import ensure_project_root
    from .state import load_sync_config, save_sync_config
    from .sync import pull_drafts, pull_library

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    # Phase 1: Git (may fail if git-http-backend not configured on server)
    click.echo("Pulling files...")
    try:
        output = git_pull(project_root)
        if output.startswith("CONFLICT"):
            click.echo("Merge conflicts detected. Resolve manually, then run 'klemma-cli pull' again.")
            click.echo(output)
            raise SystemExit(1)
        click.echo(output or "Already up to date.")
    except Exception:
        click.echo("Git pull skipped (server git transport not configured).")

    # Phase 2: Library
    click.echo("Pulling library data...")
    client = KlemmaClient(api_url=config.api_url)
    result = pull_library(client, project_root, since=config.last_pull or None)
    click.echo(
        f"Pulled {result['sources']} sources, "
        f"{result['fragments']} fragments"
    )

    # Phase 3: Drafts (dashboard edits → local draft/)
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

    # Update last_pull timestamp
    config.last_pull = datetime.now(timezone.utc).isoformat()
    save_sync_config(project_root, config)


def _resolve_project_id(config: object) -> str:
    """Get the namespaced project_id (username/project) from config.

    Falls back to extracting from git_url if project_id lacks a slash.
    """
    pid = config.project_id  # type: ignore[attr-defined]
    if "/" in pid:
        return pid
    # Extract from git_url: https://litresearch.ru/git/username/project.git
    git_url = config.git_url  # type: ignore[attr-defined]
    if "/git/" in git_url:
        path = git_url.split("/git/", 1)[1].removesuffix(".git")
        if "/" in path:
            return path
    return pid


@cli.command("status")
def sync_status() -> None:
    """Show sync status — local changes + remote diff + library counts."""
    from .client import KlemmaClient  # noqa: I001
    from .gitops import remote_log, status as git_status
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

    # Remote changes
    remote = remote_log(project_root)
    click.echo("\n=== Remote changes (not pulled) ===")
    if remote:
        for line in remote:
            click.echo(f"  {line}")
    else:
        click.echo("  (up to date)")

    # Library status from server
    click.echo("\n=== Library ===")
    try:
        client = KlemmaClient(api_url=config.api_url)
        project_id = _resolve_project_id(config)
        resp = client.get(f"/sync/status/{project_id}")
        data = resp.json()
        click.echo(f"  Sources: {data['source_count']}")
        click.echo(f"  Fragments: {data['fragment_count']}")
        if data.get("last_commit"):
            click.echo(f"  Last commit: {data['last_commit']} ({data.get('last_commit_date', '')})")
    except Exception as e:
        click.echo(f"  (could not reach server: {e})")


@cli.command()
@click.argument("n", type=int, default=1)
def rollback(n: int) -> None:
    """Rollback the last N commits (files only, not library data).

    Creates revert commits and force-pushes to the server.
    """
    from .gitops import force_push, log, revert_last_n
    from .project import ensure_project_root
    from .state import load_sync_config

    project_root = ensure_project_root()
    config = load_sync_config(project_root)
    if not config:
        click.echo("Not linked. Run 'klemma-cli link' first.", err=True)
        raise SystemExit(1)

    # Show recent history
    history = log(project_root, count=10)
    click.echo("Recent history:")
    for entry in history:
        click.echo(f"  {entry}")

    if n < 1:
        click.echo("Nothing to rollback.")
        return

    click.echo(f"\nRolling back {n} commit(s)...")
    try:
        revert_last_n(project_root, n)
        force_push(project_root)
        click.echo(f"Rolled back {n} commit(s) and pushed to server.")
    except Exception as e:
        click.echo(f"Rollback failed: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option("--api-url", default=None, help="API base URL (defaults to saved URL)")
@click.option("--email", default=None, help="Account email")
@click.option("--password", default=None, help="Account password")
def login(api_url: str | None, email: str | None, password: str | None) -> None:
    """Refresh auth tokens without changing git remotes or sync config.

    Use when 'klemma-cli push' or 'pull' returns 401 Unauthorized.
    This happens when the server restarts and invalidates old refresh tokens.
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


if __name__ == "__main__":
    cli()
