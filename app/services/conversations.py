"""Conversation memory — persist chats and replay windowed history (Step 5 foundation).

A chat is a `conversations` row; each turn is a `messages` row (`user` / `assistant`).
This lets a user start a chat, leave, and **continue it later** with context intact,
and lets the synthesis agent see recent turns so follow-ups/refinements work
("no, the other contract", "just the 2024 ones").

History is **windowed** (last N turns), not unbounded: a long chat naturally drifts,
and the user's own refinement is the correction mechanism — so we keep the prompt
small rather than summarizing. Everything is `account_id`-scoped.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from app.db.models import Conversation, Message, RetrievalTrace
from app.db.session import SessionLocal

# Default conversation context: the last 12 turns (~6 exchanges).
_HISTORY_TURNS = 12


def create_conversation(
    account_id: uuid.UUID, *, user_id: uuid.UUID | None = None,
    title: str | None = None, db=None,
) -> uuid.UUID:
    """Start a new chat; return its id."""
    own = db is None
    db = db or SessionLocal()
    try:
        convo = Conversation(account_id=account_id, user_id=user_id, title=title)
        db.add(convo)
        db.commit()
        return convo.id
    finally:
        if own:
            db.close()


def add_message(
    db, account_id: uuid.UUID, conversation_id: uuid.UUID, role: str, content: str,
) -> uuid.UUID:
    """Append one turn to a conversation (caller controls the session/commit).

    Verifies the conversation belongs to `account_id` (never cross-scope) and
    bumps the conversation's `updated_at` for recency ordering.
    """
    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.account_id != account_id:
        raise ValueError("Conversation not found for this account.")
    # Set created_at explicitly: messages added in one transaction would otherwise
    # share now() (the transaction timestamp), and uuid ids aren't monotonic — so
    # an explicit wall-clock stamp keeps user→assistant order deterministic.
    now = dt.datetime.now(dt.timezone.utc)
    message = Message(
        account_id=account_id, conversation_id=conversation_id,
        role=role, content=content, created_at=now,
    )
    db.add(message)
    convo.updated_at = now
    db.flush()
    return message.id


def load_history(
    db, account_id: uuid.UUID, conversation_id: uuid.UUID, *, limit: int = _HISTORY_TURNS,
) -> list[dict]:
    """Return the last `limit` turns (oldest-first) as ``{role, content}`` dicts.

    Empty for a brand-new or unknown/other-account conversation.
    """
    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.account_id != account_id:
        return []
    rows = db.scalars(
        select(Message)
        .where(
            Message.account_id == account_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    ).all()
    return [{"role": m.role, "content": m.content or ""} for m in reversed(rows)]


def record_trace(db, account_id: uuid.UUID, message_id: uuid.UUID, result) -> None:
    """Persist one `retrieval_traces` row for an answered message (caller commits).

    Captures what the agent did and what it cost (from the `SynthesisResult`) so the
    answer is auditable and a rating can later attach to a concrete retrieval.
    """
    trace = RetrievalTrace(
        account_id=account_id,
        message_id=message_id,
        query_text=result.query,
        intent=result.intent or None,
        retrieval_plan={
            "searches": result.searches,
            "documents_looked_up": result.documents_looked_up,
            "candidates_seen": result.candidates_seen,
            "supported": result.supported,
        },
        answer=result.answer,
        citations=[
            {
                "fact_id": str(c.fact_id) if c.fact_id else None,
                "document_id": str(c.document_id),
                "title": c.title,
                "page": c.page,
            }
            for c in result.citations
        ],
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        latency_ms=result.latency_ms,
    )
    db.add(trace)


def chat(
    account_id: uuid.UUID,
    user_message: str,
    *,
    conversation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    db=None,
):
    """One conversational turn: load history → answer → persist both messages.

    Creates the conversation if `conversation_id` is None. Returns
    ``(SynthesisResult, conversation_id, assistant_message_id)``. The agent sees the
    prior turns (not the just-sent message), so follow-ups and corrections work; pass
    `document_ids` to scope the answer to specific documents. Writes one
    `retrieval_traces` row for the assistant turn.
    """
    from app.services.synthesis import synthesize  # local import avoids a cycle

    own = db is None
    db = db or SessionLocal()
    try:
        if conversation_id is None:
            convo = Conversation(account_id=account_id, user_id=user_id)
            db.add(convo)
            db.flush()
            conversation_id = convo.id

        history = load_history(db, account_id, conversation_id)
        result = synthesize(
            user_message, account_id, history=history, db=db, document_ids=document_ids
        )

        add_message(db, account_id, conversation_id, "user", user_message)
        assistant_message_id = add_message(
            db, account_id, conversation_id, "assistant", result.answer
        )
        record_trace(db, account_id, assistant_message_id, result)
        db.commit()
        return result, conversation_id, assistant_message_id
    finally:
        if own:
            db.close()
