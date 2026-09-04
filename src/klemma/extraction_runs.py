"""Run lifecycle for chunked extraction (plan C2 / ADR-020).

The pure engine (``skills/extract_engine``) knows nothing about storage. This
module owns the three-step publication protocol across the three-tier stores:

* **step 0** — before the first AI call: a UUID ``attempt_id``, a
  deterministic ``request_fingerprint`` and a ``running`` row in
  ``project_extraction_runs`` carrying every launch condition, so a failed
  run is reproducible even when nothing reached library.db;
* **step 1** — idempotent write to library.db: ``extraction_attempts``,
  canonical ``fragments`` (content-hash ids), ``extraction_attempt_fragments``
  with spans;
* **step 2** — ONE transaction in project.db: run links, project rows,
  run status, and — only for a complete, validated run — the switch of
  ``project_sources.active_run_id`` (cross-DB integrity verified first);
* **step 3** — vault note, outside any transaction (caller's job).

Two SQLite files cannot share a transaction (no ATTACH, ADR-014); a crash
between steps 1 and 2 leaves orphan attempt rows in library.db that no
project references — harmless, listed by ``klemma repair --scan``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from .hashing import compute_content_hash
from .models import FragmentRecord

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "2"  # bump when the engine's algorithm changes (fingerprint input)


def canonical_config_json(config_ai: Any) -> str:
    """Stable JSON of the extraction-relevant AIConfig fields."""
    keys = (
        "model", "chunk_size", "chunk_overlap", "min_chunk_chars", "max_tokens_cap",
        "budget_max_input_tokens", "budget_max_output_tokens", "budget_max_cost_usd",
        "language",
    )
    payload = {k: getattr(config_ai, k, None) for k in keys}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def request_fingerprint(
    *,
    paper_id: str,
    source_content_hash: str,
    rendered_prompt_hash: str,
    outline_hash: str,
    ai_model: str,
    klemma_version: str,
    extractor_version: str,
    config_json: str,
) -> str:
    """Deterministic identity of "the same request" — for finding repeats and
    for the eval report. Distinct from ``attempt_id`` (one per actual run)."""
    material = "|".join([
        paper_id, source_content_hash, rendered_prompt_hash, outline_hash, ai_model,
        klemma_version, extractor_version, config_json,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def source_content_hash(pages: list[str]) -> str:
    h = hashlib.sha256()
    for p in pages:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def new_attempt_id() -> str:
    return str(uuid.uuid4())


@dataclass
class RunHandle:
    """What step 0 produced; carried through extraction into publication."""

    run_id: int
    attempt_id: str
    fingerprint: str
    paper_id: str
    citekey: str
    user_id: Optional[str] = None


def start_run(
    *,
    project_store,
    paper_store,
    citekey: str,
    paper_id: str,
    pages: list[str],
    config_ai: Any,
    prompt_name: str,
    template_hash: str,
    outline_hash: str = "",
    mode: str = "standard",
    klemma_version: str = "",
    user_id: Optional[str] = None,
) -> RunHandle:
    """Step 0. Insert the ``running`` row (and the library attempt) before any AI call."""
    config_json = canonical_config_json(config_ai)
    content_hash = source_content_hash(pages)
    attempt_id = new_attempt_id()
    fp = request_fingerprint(
        paper_id=paper_id,
        source_content_hash=content_hash,
        rendered_prompt_hash=template_hash,  # refined after render (see finalize_prompt_hash)
        outline_hash=outline_hash,
        ai_model=getattr(config_ai, "model", "") or "",
        klemma_version=klemma_version,
        extractor_version=EXTRACTOR_VERSION,
        config_json=config_json,
    )
    fields = dict(
        paper_id=paper_id,
        attempt_id=attempt_id,
        request_fingerprint=fp,
        mode=mode,
        prompt_name=prompt_name,
        template_hash=template_hash,
        ai_model=getattr(config_ai, "model", "") or "",
        klemma_version=klemma_version,
        extractor_version=EXTRACTOR_VERSION,
        source_content_hash=content_hash,
        outline_hash=outline_hash,
        config_json=config_json,
    )
    run_id = project_store.start_run(citekey, user_id=user_id, **fields)
    if paper_store is not None:
        try:
            paper_store.start_attempt(
                attempt_id, paper_id,
                request_fingerprint=fp, prompt_name=prompt_name, template_hash=template_hash,
                ai_model=fields["ai_model"], extractor_version=EXTRACTOR_VERSION,
                klemma_version=klemma_version, mode=mode, source_content_hash=content_hash,
                chunk_size=getattr(config_ai, "chunk_size", None),
                chunk_overlap=getattr(config_ai, "chunk_overlap", None),
                min_chunk_chars=getattr(config_ai, "min_chunk_chars", None),
                config_json=config_json,
            )
        except Exception as exc:  # noqa: BLE001 — library attempt is best-effort at step 0
            logger.warning("start_attempt failed for %s: %s", citekey, exc)
    return RunHandle(run_id=run_id, attempt_id=attempt_id, fingerprint=fp,
                     paper_id=paper_id, citekey=citekey, user_id=user_id)


def fail_run(project_store, paper_store, handle: RunHandle, error: str, **counters) -> None:
    project_store.fail_run(handle.run_id, error, **counters)
    if paper_store is not None:
        try:
            paper_store.finish_attempt(handle.attempt_id, status="failed")
        except Exception as exc:  # noqa: BLE001
            logger.debug("finish_attempt(failed) for %s: %s", handle.citekey, exc)


def publish_run(
    *,
    project_store,
    paper_store,
    handle: RunHandle,
    result: Any,
    replace_legacy: bool = False,
) -> str:
    """Steps 1 and 2. ``result`` is the ``ExtractionResult`` from ``extract_fragments``.

    Returns the run status: ``published`` | ``pending``. Raises on integrity
    failure (the project transaction is rolled back and the run marked
    ``failed, error=integrity`` by the store).
    """
    fragments = list(result.fragments)
    spans = list(getattr(result, "spans", []) or [])
    statuses = list(getattr(result, "verbatim_statuses", []) or [])
    locators = list(getattr(result, "source_locators", []) or [])
    spans += [None] * (len(fragments) - len(spans))
    statuses += ["unverified"] * (len(fragments) - len(statuses))
    locators += [None] * (len(fragments) - len(locators))

    records: list[FragmentRecord] = []
    links: list[dict] = []
    project_rows: list[dict] = []
    for f, span, vstatus, loc in zip(fragments, spans, statuses, locators):
        fid = compute_content_hash(handle.paper_id, f.text, f.page)
        records.append(FragmentRecord(
            fragment_id=fid, paper_id=handle.paper_id, fragment_text=f.text,
            fragment_type=f.type, page_number=f.page, citation_intent=f.citation_intent,
            verbatim=f.verbatim, content_hash=fid,
        ))
        links.append({
            "char_start": span[0] if span else None,
            "char_end": span[1] if span else None,
            "source_locator": loc,
            "verbatim_status": vstatus,
        })
        project_rows.append({
            "fragment_id": fid,
            "relevance_score": f.relevance,
            "usage_hint": f.usage_hint,
            "model_section": f.section,
            "chapter": f.chapter,
            "verbatim_status": vstatus,
        })

    # Finalize identity from the ACTUAL request: rendered prompt (context, tags,
    # metadata) and the model the task routing used (Codex P1 on PR-B).
    rendered_hash = getattr(result, "rendered_prompt_hash", "") or getattr(result, "prompt_hash", "")
    actual_model = getattr(result, "model", "") or ""
    run_row = project_store.get_run(handle.run_id) or {}
    fp = request_fingerprint(
        paper_id=handle.paper_id,
        source_content_hash=run_row.get("source_content_hash") or "",
        rendered_prompt_hash=rendered_hash,
        outline_hash=run_row.get("outline_hash") or "",
        ai_model=actual_model,
        klemma_version=run_row.get("klemma_version") or "",
        extractor_version=EXTRACTOR_VERSION,
        config_json=run_row.get("config_json") or "",
    )
    project_store.set_run_identity(
        handle.run_id, prompt_hash=rendered_hash, ai_model=actual_model, request_fingerprint=fp,
    )
    handle.fingerprint = fp
    if paper_store is not None:
        try:
            paper_store.set_attempt_identity(
                handle.attempt_id, prompt_hash=rendered_hash, ai_model=actual_model,
                request_fingerprint=fp,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_attempt_identity failed for %s: %s", handle.citekey, exc)

    is_partial = bool(getattr(result, "failed_chunks", 0)) or (
        float(getattr(result, "coverage_ratio", 1.0) or 1.0) < 1.0
    )
    validation_incomplete = bool(getattr(result, "validation_incomplete", False))
    coverage_json = json.dumps({
        "ratio": float(getattr(result, "coverage_ratio", 1.0) or 1.0),
        "chunk_total": int(getattr(result, "chunk_total", 1) or 1),
        "failed_chunks": int(getattr(result, "failed_chunks", 0) or 0),
    })

    # Step 1 — library.db (idempotent).
    linked_ids: set[str] = set()
    if paper_store is not None:
        paper_store.save_attempt_fragments(handle.attempt_id, handle.paper_id, records, links)
        paper_store.finish_attempt(
            handle.attempt_id,
            status="published" if not (is_partial or validation_incomplete) else "pending",
            coverage_json=coverage_json,
            validation_incomplete=validation_incomplete,
        )
        linked_ids = {r["fragment_id"] for r in paper_store.get_attempt_fragments(handle.attempt_id)}

    def _verify(fid: str) -> bool:
        return paper_store is None or fid in linked_ids

    # Step 2 — project.db, one transaction.
    status = project_store.publish_run(
        handle.run_id,
        project_rows,
        is_partial=is_partial,
        validation_incomplete=validation_incomplete,
        counters={
            "coverage_json": coverage_json,
            "chunk_count": int(getattr(result, "chunk_total", 1) or 1),
            "failed_chunks": int(getattr(result, "failed_chunks", 0) or 0),
            "tokens_in": int(getattr(result, "tokens_in", 0) or 0),
            "tokens_out": int(getattr(result, "tokens_out", 0) or 0),
            "cost_usd": getattr(result, "cost_usd", None),
            "attempt_id": handle.attempt_id,
        },
        verify_fragment=_verify,
        replace_legacy=replace_legacy,
    )
    logger.info(
        "run %d for %s → %s (%d fragments, partial=%s, validation_incomplete=%s)",
        handle.run_id, handle.citekey, status, len(project_rows), is_partial,
        validation_incomplete,
    )
    return status
