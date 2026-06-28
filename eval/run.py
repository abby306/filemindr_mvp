"""Eval runner: score a retrieval callable against the gold set.

    python -m eval.run                 # runs the built-in stub (fixtures)
    python -m eval.run --k 3

Phase 5 wiring: pass any ``retrieve(query: str) -> RetrievedAnswer`` to
`run_eval`. The real engine's ``retrieve(query, account_id)`` is adapted by
binding the eval account id (see eval/README.md); nothing else changes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from eval.schema import GoldQuery, RetrievedAnswer, load_gold
from eval.scorers import score_dataset

GOLD_PATH = Path(__file__).resolve().parent / "gold" / "seed.yaml"

Retrieve = Callable[[str], RetrievedAnswer]


def run_eval(retrieve: Retrieve, gold: list[GoldQuery], *, k: int = 5) -> dict:
    """Run `retrieve` over every gold query and score the results."""
    results = {query.id: retrieve(query.query) for query in gold}
    return score_dataset(gold, results, k=k)


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def print_report(scores: dict) -> None:
    """Pretty-print per-type and overall scores."""
    header = f"{'group':<12} {'doc_recall':>11} {'fact_recall':>12} {'answer':>8}"
    print(f"\nRetrieval eval — n={scores['n']} queries, k={scores['k']}")
    print(header)
    print("-" * len(header))
    for type_, metrics in sorted(scores["by_type"].items()):
        print(
            f"{type_:<12} {_fmt(metrics['doc_recall']):>11} "
            f"{_fmt(metrics['fact_recall']):>12} {_fmt(metrics['answer_correctness']):>8}"
        )
    o = scores["overall"]
    print("-" * len(header))
    print(
        f"{'OVERALL':<12} {_fmt(o['doc_recall']):>11} "
        f"{_fmt(o['fact_recall']):>12} {_fmt(o['answer_correctness']):>8}\n"
    )


# --- built-in stub retrieval (fixtures, so the runner works before Phase 5) -
# Maps each gold query to a plausible retrieval result. Intentionally imperfect
# (moodump_stack omits the pgvector fact) so the scores are non-trivial.
_STUB_FIXTURES: dict[str, RetrievedAnswer] = {
    "How much did I spend at SM Supermarket?": RetrievedAnswer(
        doc_ids=["sm-supermarket-receipt"],
        facts=["The total amount is PHP 189.00.", "The customer paid PHP 200.00 cash."],
        answer="You spent PHP 189.00 at SM Supermarket.",
    ),
    "What was the VAT on the SM Supermarket receipt?": RetrievedAnswer(
        doc_ids=["sm-supermarket-receipt"],
        facts=["The VAT (12%) amount is PHP 20.25."],
        answer="The VAT was PHP 20.25.",
    ),
    "What is the invoice number on the Mercury Drug receipt?": RetrievedAnswer(
        doc_ids=["mercury-drug-receipt"],
        facts=["The invoice number is 104044328167."],
        answer="The invoice number is 104044328167.",
    ),
    "Which document is issued by SUPERVALUE, INC.?": RetrievedAnswer(
        doc_ids=["sm-supermarket-receipt"],
        facts=["The sales invoice is from SM Supermarket, SUPERVALUE, INC."],
        answer="The SM Supermarket receipt is issued by SUPERVALUE, INC.",
    ),
    "Who are the parties to the Viridian NDA?": RetrievedAnswer(
        doc_ids=["viridian-nda"],
        facts=["This Agreement is entered into between Viridian Energy Management, LLC and Abdullah Asad."],
        answer="The parties are Viridian Energy Management, LLC and Abdullah Asad.",
    ),
    "How long does the Viridian NDA stay in effect?": RetrievedAnswer(
        doc_ids=["viridian-nda"],
        facts=["The Agreement shall remain in effect for five years from the Effective Date."],
        answer="It stays in effect for five years from the Effective Date.",
    ),
    "What technology stack does moodump use?": RetrievedAnswer(
        doc_ids=["moodump-plan"],
        facts=["The stack includes Python 3.12, FastAPI + Pydantic v2, LangGraph."],  # omits pgvector
        answer="moodump uses Python 3.12 and FastAPI, among others.",
    ),
    "What is the moodump subscription price?": RetrievedAnswer(
        doc_ids=["moodump-plan"],
        facts=["The subscription price is $20/month, yielding a gross margin of ~75-80%."],
        answer="The subscription price is $20/month.",
    ),
}


def _stub_retrieve(query: str) -> RetrievedAnswer:
    return _STUB_FIXTURES.get(query, RetrievedAnswer())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the retrieval eval.")
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff for recall")
    parser.add_argument("--gold", default=str(GOLD_PATH), help="path to a gold YAML file")
    args = parser.parse_args()

    gold = load_gold(args.gold)
    scores = run_eval(_stub_retrieve, gold, k=args.k)
    print_report(scores)


if __name__ == "__main__":
    main()
