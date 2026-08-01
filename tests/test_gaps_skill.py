"""Tests for the citation-graph rerank skill (skills/gaps.py)."""

from types import SimpleNamespace

from klemma.skills.gaps import find_citation_gaps


def _cand(oa_id, title, doi="", abstract="", relation="cites"):
    return SimpleNamespace(
        openalex_id=oa_id,
        doi=doi,
        title=title,
        abstract=abstract,
        year=2020,
        venue="GRL",
        cited_by=10,
        first_author="Doe",
        relation=relation,
    )


class FakeEmbeddings:
    """Returns canned vectors keyed by the exact text embedded; records calls."""

    model_name = "test"
    dim = 3

    def __init__(self, mapping):
        self._m = mapping
        self.seen = []

    def embed_batch(self, texts):
        self.seen.extend(texts)
        return [self._m.get(t, [0.0, 0.0, 1.0]) for t in texts]


def test_suppresses_owned_by_doi():
    cands = [_cand("W1", "Owned Paper", doi="10.1/owned"), _cand("W2", "Fresh Paper", doi="10.2/new")]
    emb = FakeEmbeddings({"Fresh Paper": [1.0, 0.0, 0.0]})
    res = find_citation_gaps(
        "seed",
        candidates=cands,
        owned_dois=["https://doi.org/10.1/OWNED"],  # mixed case + url form
        owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0],
        embeddings=emb,
    )
    assert res.n_neighbours == 2
    assert res.n_owned_suppressed == 1
    assert [g.openalex_id for g in res.gaps] == ["W2"]


def test_suppresses_owned_by_title():
    cands = [_cand("W1", "Predictability of the Arctic Sea Ice Edge"), _cand("W2", "Fresh Paper")]
    emb = FakeEmbeddings({"Fresh Paper": [1.0, 0.0, 0.0]})
    res = find_citation_gaps(
        "seed",
        candidates=cands,
        owned_dois=[],
        owned_titles=["predictability of the arctic sea-ice edge!"],  # normalizes to same
        seed_vector=[1.0, 0.0, 0.0],
        embeddings=emb,
    )
    assert res.n_owned_suppressed == 1
    assert [g.openalex_id for g in res.gaps] == ["W2"]


def test_ranks_by_cosine_to_seed():
    cands = [_cand("W1", "Far"), _cand("W2", "Near"), _cand("W3", "Mid")]
    emb = FakeEmbeddings({
        "Far": [0.0, 1.0, 0.0],   # orthogonal → 0.0
        "Near": [1.0, 0.0, 0.0],  # identical → 1.0
        "Mid": [1.0, 1.0, 0.0],   # ~0.707
    })
    res = find_citation_gaps(
        "seed", candidates=cands, owned_dois=[], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb,
    )
    assert [g.openalex_id for g in res.gaps] == ["W2", "W3", "W1"]
    assert res.gaps[0].similarity > 0.99


def test_both_relation_preserved():
    cands = [_cand("W1", "Fresh", relation="both")]
    emb = FakeEmbeddings({"Fresh": [1.0, 0.0, 0.0]})
    res = find_citation_gaps(
        "seed", candidates=cands, owned_dois=[], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb,
    )
    assert res.gaps[0].relation == "both"


def test_deep_embeds_abstract():
    cands = [_cand("W1", "Title", abstract="the abstract body")]
    emb = FakeEmbeddings({})
    find_citation_gaps(
        "seed", candidates=cands, owned_dois=[], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb, deep=True,
    )
    assert emb.seen == ["Title. the abstract body"]


def test_title_only_by_default():
    cands = [_cand("W1", "Title", abstract="the abstract body")]
    emb = FakeEmbeddings({})
    find_citation_gaps(
        "seed", candidates=cands, owned_dois=[], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb,
    )
    assert emb.seen == ["Title"]


def test_limit_caps_output():
    cands = [_cand(f"W{i}", f"Paper {i}") for i in range(20)]
    emb = FakeEmbeddings({f"Paper {i}": [1.0, 0.0, 0.0] for i in range(20)})
    res = find_citation_gaps(
        "seed", candidates=cands, owned_dois=[], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb, limit=5,
    )
    assert len(res.gaps) == 5
    assert res.n_neighbours == 20


def test_all_owned_returns_empty():
    cands = [_cand("W1", "Owned", doi="10.1/x")]
    emb = FakeEmbeddings({})
    res = find_citation_gaps(
        "seed", candidates=cands, owned_dois=["10.1/x"], owned_titles=[],
        seed_vector=[1.0, 0.0, 0.0], embeddings=emb,
    )
    assert res.gaps == []
    assert res.n_owned_suppressed == 1
    assert emb.seen == []  # nothing to embed
