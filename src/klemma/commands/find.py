"""klemma find-source — обратный поиск источника под утверждение."""
from __future__ import annotations

import json
import sys

import click
from rich.table import Table

from ..cli import _get_context, console, main

_PREVIEW_CHARS = 160


def _rank_candidates(
    frag_hits: list[dict],
    source_sims: dict[str, float],
    top_k: int,
) -> list[dict]:
    """Merge fragment- and source-level hits into a ranked per-source list.

    A source's score is the best available signal — its closest fragment or
    its source-level embedding, whichever is higher. Sources visible only at
    source level (no embedded fragments) still surface, just without a quote
    preview: the fragment is what makes the hit checkable.
    """
    candidates: dict[str, dict] = {}
    for f in frag_hits:
        ck = f["citekey"]
        cur = candidates.get(ck)
        if cur is not None and f["similarity"] <= cur["fragment_similarity"]:
            continue
        page = f.get("page_number")
        candidates[ck] = {
            "citekey": ck,
            "fragment_similarity": f["similarity"],
            "source_similarity": source_sims.get(ck),
            "locator": f.get("source_locator")
            or (f"с. {page}" if page is not None else None),
            "preview": (f.get("fragment_text") or "").strip(),
            "verbatim": bool(f.get("verbatim")),
        }

    for ck, sim in source_sims.items():
        if ck not in candidates:
            candidates[ck] = {
                "citekey": ck,
                "fragment_similarity": None,
                "source_similarity": round(sim, 4),
                "locator": None,
                "preview": None,
                "verbatim": False,
            }

    for c in candidates.values():
        c["similarity"] = round(
            max(c["fragment_similarity"] or 0.0, c["source_similarity"] or 0.0), 4
        )

    ranked = sorted(candidates.values(), key=lambda c: c["similarity"], reverse=True)
    return ranked[:top_k]


@main.command("find-source")
@click.argument("claim_text")
@click.option("-k", "--top-k", default=10, show_default=True, help="Сколько источников показать")
@click.option("--json", "as_json", is_flag=True, help="Вывод в JSON вместо таблицы")
@click.pass_context
def find_source(ctx, claim_text, top_k, as_json):
    """Найти источники библиотеки, лучше всего поддерживающие утверждение.

    CLAIM_TEXT: текст утверждения (предложение из рукописи).

    Ловит класс «верное утверждение под неверной ссылкой»: проверка существующей
    ссылки отвечает «источник не подтверждает», а обратный поиск показывает,
    где в библиотеке лежит настоящее подтверждение. Ранжирует источники по
    лучшему сигналу — ближайшему фрагменту или source-эмбеддингу; verbatim-метка
    отличает дословную цитату от пересказа (пересказ годится, чтобы найти место
    в источнике, но не годится, чтобы его процитировать).

    Example:
      klemma find-source "для U-Net предложены функции потерь, учитывающие ошибку кромки"
    """
    from ..embeddings import cosine_similarity

    kctx = _get_context(ctx)
    state = kctx.state
    emb = kctx.embeddings

    if not emb:
        console.print(
            "[yellow]Эмбеддинги не настроены (embeddings.backend в config.yaml) — "
            "обратный поиск недоступен.[/yellow]"
        )
        return

    model_name = emb.model_name

    # Coverage of the active model over the corpus — a silent model switch
    # would otherwise shrink the search space without any visible sign.
    frag_stats = state.get_fragment_embedding_stats()
    src_stats = state.get_embedding_stats()
    frag_covered = frag_stats["models"].get(model_name, 0)
    src_covered = src_stats["models"].get(model_name, 0)

    if frag_covered == 0 and src_covered == 0:
        other_models = sorted(set(frag_stats["models"]) | set(src_stats["models"]))
        if other_models:
            console.print(
                f"[yellow]Корпус эмбеддирован другими моделями ({', '.join(other_models)}), "
                f"активная — {model_name}. Запустите `klemma embed all --remodel`.[/yellow]"
            )
        else:
            console.print(
                "[yellow]В корпусе нет эмбеддингов — запустите `klemma embed all`.[/yellow]"
            )
        return

    if frag_covered < frag_stats["total"] or src_covered < src_stats["total"]:
        console.print(
            f"[yellow]Покрытие модели {model_name}: "
            f"фрагменты {frag_covered}/{frag_stats['total']}, "
            f"источники {src_covered}/{src_stats['total']} — "
            f"часть корпуса вне поиска (`klemma embed all --remodel`).[/yellow]"
        )

    try:
        query_vec = emb.embed(claim_text)
    except Exception as exc:
        console.print(f"[red]Не удалось получить эмбеддинг запроса: {exc}[/red]")
        sys.exit(2)
    if not query_vec:
        console.print("[red]Провайдер эмбеддингов вернул пустой вектор для запроса.[/red]")
        sys.exit(2)

    frag_hits = state.retrieve_similar_fragments(
        query_vec, top_k=top_k * 3, model=model_name
    )
    source_emb = state.get_all_embeddings(model=model_name)
    source_sims = {
        sid: cosine_similarity(query_vec, vec) for sid, vec in source_emb.items()
    }

    ranked = _rank_candidates(frag_hits, source_sims, top_k)

    if as_json:
        click.echo(json.dumps(
            {"query": claim_text, "model": model_name, "candidates": ranked},
            ensure_ascii=False, indent=2,
        ))
        return

    if not ranked:
        console.print("[yellow]Похожих источников не найдено.[/yellow]")
        return

    console.print(f"\n[bold]Кандидаты под утверждение[/bold] [dim]({model_name})[/dim]\n")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Citekey", style="cyan", width=28)
    table.add_column("Sim", justify="right", width=7)
    table.add_column("Где", width=12)
    table.add_column("Фрагмент", overflow="fold")

    for i, c in enumerate(ranked, 1):
        if c["preview"]:
            preview = c["preview"]
            if len(preview) > _PREVIEW_CHARS:
                preview = preview[:_PREVIEW_CHARS].rstrip() + "…"
            mark = "[green]verbatim[/green]" if c["verbatim"] else "[dim]пересказ[/dim]"
            preview = f"{mark} «{preview}»"
        else:
            preview = "[dim]— только source-эмбеддинг, фрагментов нет[/dim]"
        table.add_row(
            str(i),
            f"@{c['citekey']}",
            f"{c['similarity']:.3f}",
            c["locator"] or "—",
            preview,
        )

    console.print(table)
