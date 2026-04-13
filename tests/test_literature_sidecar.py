"""Tests for `klemma.literature.sidecar.write_pdf_sidecar`.

Exercises the three stable format contracts downstream consumers rely on:
path layout, `<!-- Page N -->` delimiter, frontmatter field set; plus
atomic-write safety and citekey validation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from klemma.literature.sidecar import write_pdf_sidecar


def _metadata() -> dict:
    return {
        "title": "Sea ice forecasting with perturbed parameters",
        "authors": "Goessling H., Jung T.",
        "year": 2018,
        "doi": "10.1007/s00382-017-4025-y",
        "source": "/tmp/goessling2018.pdf",
    }


def test_write_pdf_sidecar_creates_file(tmp_path: Path) -> None:
    pages = [
        "First page prose\nsecond line.",
        "Methods section start.",
        "Results and discussion.",
    ]

    target = write_pdf_sidecar(tmp_path, "goessling2018", pages, _metadata())

    assert target == tmp_path / ".klemma" / "pdfs" / "goessling2018.md"
    assert target.exists()
    text = target.read_text(encoding="utf-8")

    # Stable frontmatter fields
    assert "# Sea ice forecasting with perturbed parameters" in text
    assert "> Citekey: goessling2018" in text
    assert "> Authors: Goessling H., Jung T." in text
    assert "> Year: 2018" in text
    assert "> DOI: 10.1007/s00382-017-4025-y" in text
    assert "> Pages: 3" in text
    assert "> Source: /tmp/goessling2018.pdf" in text

    # Page 1 has no marker; pages 2+ delimited by `<!-- Page N -->`
    assert "<!-- Page 1 -->" not in text
    markers = re.findall(r"<!-- Page (\d+) -->", text)
    assert markers == ["2", "3"]

    # Content order preserved
    assert text.index("First page prose") < text.index("<!-- Page 2 -->")
    assert text.index("<!-- Page 2 -->") < text.index("Methods section start.")
    assert text.index("Methods section start.") < text.index("<!-- Page 3 -->")
    assert text.index("<!-- Page 3 -->") < text.index("Results and discussion.")


def test_write_pdf_sidecar_single_page_has_no_marker(tmp_path: Path) -> None:
    target = write_pdf_sidecar(tmp_path, "single2020", ["only page"], _metadata())
    text = target.read_text(encoding="utf-8")
    assert "<!-- Page" not in text
    assert "> Pages: 1" in text
    assert "only page" in text


def test_write_pdf_sidecar_atomic(tmp_path: Path) -> None:
    """Simulate a write crash mid-flight: no partial file must remain."""
    target_dir = tmp_path / ".klemma" / "pdfs"

    def _boom(src: str, dst: str) -> None:  # noqa: ARG001
        raise OSError("simulated crash")

    with patch("klemma.literature.sidecar.os.replace", side_effect=_boom):
        with pytest.raises(OSError, match="simulated crash"):
            write_pdf_sidecar(tmp_path, "crashtest2021", ["page"], _metadata())

    assert not (target_dir / "crashtest2021.md").exists()
    leftovers = [p for p in target_dir.iterdir() if p.name.endswith(".md.tmp")]
    assert leftovers == []


def test_write_pdf_sidecar_idempotent(tmp_path: Path) -> None:
    write_pdf_sidecar(tmp_path, "repeat2022", ["v1 page"], _metadata())
    second = write_pdf_sidecar(tmp_path, "repeat2022", ["v2 page"], _metadata())

    text = second.read_text(encoding="utf-8")
    assert "v2 page" in text
    assert "v1 page" not in text

    pdfs_dir = tmp_path / ".klemma" / "pdfs"
    files = sorted(p.name for p in pdfs_dir.iterdir())
    assert files == ["repeat2022.md"]


@pytest.mark.parametrize(
    "bad",
    ["", "..", "../evil", "a/b", "c\\d", "..\\sneaky"],
)
def test_write_pdf_sidecar_rejects_bad_citekey(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid citekey"):
        write_pdf_sidecar(tmp_path, bad, ["page"], _metadata())


def test_write_pdf_sidecar_creates_missing_directories(tmp_path: Path) -> None:
    nested = tmp_path / "fresh_project"
    assert not (nested / ".klemma").exists()
    target = write_pdf_sidecar(nested, "fresh2024", ["page"], _metadata())
    assert target.parent == nested / ".klemma" / "pdfs"
    assert target.exists()


def test_write_pdf_sidecar_handles_missing_metadata(tmp_path: Path) -> None:
    target = write_pdf_sidecar(tmp_path, "minimal2023", ["page"], {})
    text = target.read_text(encoding="utf-8")
    # Falls back to citekey as title; missing fields render as em dash
    assert "# minimal2023" in text
    assert "> Authors: —" in text
    assert "> Year: —" in text
    assert "> DOI: —" in text
    assert "> Source: —" in text


def test_write_pdf_sidecar_file_permissions_preserved(tmp_path: Path) -> None:
    """Atomic replace should leave the target readable/writable by the user."""
    target = write_pdf_sidecar(tmp_path, "perms2020", ["page"], _metadata())
    mode = os.stat(target).st_mode & 0o600
    assert mode == 0o600
