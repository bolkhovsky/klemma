"""Bibliography commands: bib export."""

import re
import sys
from pathlib import Path

import click

from ..cli import _get_context, main


def _parse_bib_entries(bib_text: str) -> dict[str, str]:
    """Parse BibTeX text into a dict of {citekey: full_entry_text}.

    Uses a simple block-matching approach: each entry starts with
    @type{citekey, and ends at the matching closing brace.
    """
    entries: dict[str, str] = {}
    # Find all entry start positions: @word{citekey,
    entry_starts = list(re.finditer(r"@\w+\s*\{([^,\s]+)\s*,", bib_text))
    for i, match in enumerate(entry_starts):
        citekey = match.group(1)
        start = match.start()
        # Entry block ends at the matching closing brace
        end = entry_starts[i + 1].start() if i + 1 < len(entry_starts) else len(bib_text)
        block = bib_text[start:end].rstrip()
        # Trim any trailing whitespace between entries
        entries[citekey] = block
    return entries


def _parse_section_range(section_str: str) -> list[str]:
    """Parse section range string into list of top-level section prefixes.

    Examples:
      "1..6"  -> ["1", "2", "3", "4", "5", "6"]
      "1..3"  -> ["1", "2", "3"]
      "2"     -> ["2"]
      "1.1"   -> ["1.1"]  (subsection — returned as-is)
    """
    range_match = re.match(r"^(\d+)\.\.(\d+)$", section_str.strip())
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        return [str(n) for n in range(start, end + 1)]
    # Single section — return as-is
    return [section_str.strip()]


def _get_citekeys_for_sections(state, section_prefixes: list[str]) -> set[str]:
    """Query DB for all source IDs assigned to the given section prefixes.

    Uses get_section_sources() which returns IDs regardless of processing status,
    so pending sources are included in bib export (they may still be in the bib file).
    """
    citekeys: set[str] = set()
    for prefix in section_prefixes:
        ids = state.get_section_sources(prefix)
        citekeys.update(ids)
    return citekeys


@main.group()
def bib():
    """Bibliography utilities.

    Subcommands:
      klemma bib export   -- export filtered .bib subset
    """


@bib.command("export")
@click.option(
    "--citekeys",
    default=None,
    help="Comma-separated citekeys to export (e.g. @smith2020,@jones2021)",
)
@click.option(
    "--section",
    default=None,
    help="Section range to export refs for (e.g. 1..6 or 2.3)",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output file path (default: stdout)",
)
@click.option(
    "--bib",
    "bib_path",
    default=None,
    type=click.Path(),
    help="Path to references.bib (overrides config)",
)
@click.pass_context
def bib_export(ctx, citekeys, section, output, bib_path):
    """Export a filtered subset of a BibTeX bibliography.

    Filter by explicit citekeys:
      klemma bib export --citekeys @smith2020,@jones2021

    Filter by section range (uses DB source assignments):
      klemma bib export --section 1..6

    Write to file instead of stdout:
      klemma bib export --citekeys @key1 --output paper.bib

    The --bib flag overrides the bib file path. If not given, the command
    looks for a .bib file adjacent to the project root (references.bib).
    """
    if not citekeys and not section:
        raise click.UsageError("Provide at least --citekeys or --section.")

    # Resolve bib file
    if not bib_path:
        kctx = _get_context(ctx)
        # Try zotero.library_json (.bib export) or project root references.bib
        lib_json = kctx.config.zotero.library_json if kctx.config.zotero else None
        if lib_json and Path(lib_json).suffix.lower() == ".bib" and Path(lib_json).exists():
            resolved_bib = Path(lib_json)
        else:
            # Fallback: look for references.bib next to project root
            project_root = kctx.project_root
            candidate = project_root / "references.bib"
            if not candidate.exists():
                raise click.ClickException(
                    "No BibTeX file found. Use --bib to specify the path to your .bib file."
                )
            resolved_bib = candidate
    else:
        resolved_bib = Path(bib_path)

    if not resolved_bib.exists():
        raise click.ClickException(f"BibTeX file not found: {resolved_bib}")

    bib_text = resolved_bib.read_text(encoding="utf-8")
    all_entries = _parse_bib_entries(bib_text)

    # Determine which citekeys to export
    wanted: set[str] = set()

    if citekeys:
        # Parse comma-separated list, strip @ prefix
        for key in citekeys.split(","):
            wanted.add(key.strip().lstrip("@"))

    if section:
        kctx = _get_context(ctx)
        prefixes = _parse_section_range(section)
        db_keys = _get_citekeys_for_sections(kctx.state, prefixes)
        wanted.update(db_keys)

    # Filter entries
    matched = {k: v for k, v in all_entries.items() if k in wanted}

    # Report any requested keys not found in bib
    missing = wanted - set(all_entries.keys())
    if missing:
        click.echo(
            f"Warning: {len(missing)} citekey(s) not found in bib: "
            f"{', '.join(sorted(missing))}",
            err=True,
        )

    result_text = "\n\n".join(matched[k] for k in sorted(matched)) + "\n"

    if output:
        Path(output).write_text(result_text, encoding="utf-8")
        click.echo(f"Exported {len(matched)} entries to {output}", err=True)
    else:
        sys.stdout.write(result_text)
