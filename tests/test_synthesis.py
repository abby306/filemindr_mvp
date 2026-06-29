"""Agentic synthesis: the retrieve→reason→answer loop.

Both seams are stubbed — `synthesis._gemini_turn` (no Gemini API) and
`retrieval.retrieve` (no DB/models) — so the loop logic, citation grounding, the
search tool, and the bounded-iteration fallback are tested deterministically.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import synthesis
from app.services.retrieval import FactHit, RetrievalResult
from app.services.synthesis import ModelTurn


def _fact(key, text, *, fact_id=None, doc=None, page=1) -> FactHit:
    return FactHit(key=key, text=text, document_id=doc or uuid.uuid4(),
                   source="vector", page=page, fact_id=fact_id)


@pytest.fixture
def no_db(monkeypatch):
    """Make synthesize run without a real session, catalog, or doc-meta lookups."""
    monkeypatch.setattr(synthesis, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(synthesis, "_load_doc_meta", lambda *a, **k: None)
    monkeypatch.setattr(
        synthesis.catalog, "corpus_overview",
        lambda db, account_id: {"total_documents": 0, "documents": []},
    )


class _FakeSession:
    def close(self): ...


def _script(monkeypatch, turns: list[ModelTurn]):
    """Drive _gemini_turn through a fixed sequence of model decisions."""
    seq = iter(turns)
    monkeypatch.setattr(
        synthesis, "_gemini_turn",
        lambda transcript, *, allow_search, model: next(seq),
    )


def _stub_retrieve(monkeypatch, *result_facts: list[FactHit]):
    """Return a RetrievalResult per successive retrieve() call."""
    seq = iter(result_facts)
    def fake(query, account_id, *, db=None, k=5, **kwargs):
        facts = next(seq)
        return RetrievalResult(query=query, intent="semantic", facts=facts,
                               doc_ids=[f.document_id for f in facts])
    monkeypatch.setattr(synthesis.retrieval, "retrieve", fake)


def test_finish_immediately_with_citation(no_db, monkeypatch) -> None:
    fid = uuid.uuid4()
    _stub_retrieve(monkeypatch, [_fact("k1", "The price is $20/month.", fact_id=fid, page=3)])
    _script(monkeypatch, [
        ModelTurn(tool="finish", args={
            "answer": "It costs $20/month.", "cited_fact_ids": ["f1"], "supported": True,
        }),
    ])

    res = synthesis.synthesize("price?", uuid.uuid4())

    assert res.supported is True
    assert res.answer == "It costs $20/month."
    assert len(res.citations) == 1
    assert res.citations[0].fact_id == fid
    assert res.citations[0].page == 3
    assert res.searches == []


def test_search_then_finish(no_db, monkeypatch) -> None:
    # First pool is thin; the agent searches, then cites a fact from the 2nd pool.
    fid = uuid.uuid4()
    _stub_retrieve(
        monkeypatch,
        [_fact("k1", "Unrelated.")],                                  # initial pool
        [_fact("k2", "The VAT is PHP 20.25.", fact_id=fid, page=1)],  # after search
    )
    _script(monkeypatch, [
        ModelTurn(tool="search", args={"query": "VAT amount"}),
        ModelTurn(tool="finish", args={
            "answer": "The VAT is PHP 20.25.", "cited_fact_ids": ["f2"], "supported": True,
        }),
    ])

    res = synthesis.synthesize("what was the vat?", uuid.uuid4())

    assert res.searches == ["VAT amount"]
    assert res.candidates_seen == 2  # both pools registered
    assert [c.fact_id for c in res.citations] == [fid]


def test_hallucinated_citation_is_dropped(no_db, monkeypatch) -> None:
    _stub_retrieve(monkeypatch, [_fact("k1", "A fact.", fact_id=uuid.uuid4())])
    _script(monkeypatch, [
        ModelTurn(tool="finish", args={
            "answer": "...", "cited_fact_ids": ["f1", "f99"],  # f99 was never offered
            "supported": True,
        }),
    ])

    res = synthesis.synthesize("q", uuid.uuid4())

    assert len(res.citations) == 1  # f99 dropped, only f1 kept


def test_unsupported_answer(no_db, monkeypatch) -> None:
    _stub_retrieve(monkeypatch, [_fact("k1", "Something irrelevant.")])
    _script(monkeypatch, [
        ModelTurn(tool="finish", args={
            "answer": "The documents don't contain that.",
            "cited_fact_ids": [], "supported": False,
        }),
    ])

    res = synthesis.synthesize("unknowable?", uuid.uuid4())

    assert res.supported is False
    assert res.citations == []


def test_bounded_loop_forces_finish(no_db, monkeypatch) -> None:
    # Model keeps searching forever; the loop must terminate with an honest miss.
    _stub_retrieve(monkeypatch, *([[_fact(f"k{i}", "noise")] for i in range(10)]))
    monkeypatch.setattr(
        synthesis, "_gemini_turn",
        lambda transcript, *, allow_search, model: ModelTurn(tool="search", args={"query": "again"}),
    )

    res = synthesis.synthesize("q", uuid.uuid4(), max_steps=3)

    assert res.supported is False
    assert "couldn't find" in res.answer.lower()
    assert len(res.searches) <= 3  # bounded


def test_tokens_accumulate(no_db, monkeypatch) -> None:
    _stub_retrieve(monkeypatch, [_fact("k1", "x", fact_id=uuid.uuid4())], [_fact("k2", "y")])
    _script(monkeypatch, [
        ModelTurn(tool="search", args={"query": "more"}, prompt_tokens=100, completion_tokens=10),
        ModelTurn(tool="finish", args={"answer": "a", "cited_fact_ids": ["f1"], "supported": True},
                  prompt_tokens=150, completion_tokens=20),
    ])

    res = synthesis.synthesize("q", uuid.uuid4())

    assert res.prompt_tokens == 250
    assert res.completion_tokens == 30
