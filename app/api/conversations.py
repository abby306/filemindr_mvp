"""Chat endpoints — create conversations, send messages, read history.

Thin wrappers over the `conversations` service: `POST /conversations` opens a chat,
`POST /conversations/{id}/messages` runs one grounded agentic turn (and persists a
`retrieval_traces` row), and `GET /conversations/{id}/messages` replays history. Every
read/write is account-scoped through `AccountScope`, so no chat can cross accounts.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.schemas import (
    CitationOut,
    ConversationOut,
    MessageAnswerOut,
    MessageCreate,
    MessageOut,
)
from app.core.scoping import AccountScope, get_current_account
from app.db.models import Conversation, Document, Message
from app.services import conversations

router = APIRouter(prefix="/api/v1", tags=["conversations"])


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    scope: AccountScope = Depends(get_current_account),
) -> ConversationOut:
    """Start a new chat for the active account."""
    convo_id = conversations.create_conversation(
        scope.account_id, user_id=scope.user.id, db=scope.db
    )
    return ConversationOut(id=convo_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageAnswerOut,
)
def post_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    scope: AccountScope = Depends(get_current_account),
) -> MessageAnswerOut:
    """Send a user message and get a grounded, cited answer.

    `scope="document"` pins the answer to `document_id` (which must belong to the
    active account). Returns 404 for an unknown/foreign conversation or document.
    """
    document_ids: list[uuid.UUID] | None = None
    if body.scope == "document":
        if body.document_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "document_id_required",
                    "message": "scope='document' requires document_id.",
                },
            )
        doc = scope.db.scalar(
            scope.select(Document).where(Document.id == body.document_id)
        )
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Document not found."},
            )
        document_ids = [body.document_id]

    try:
        result, _, message_id = conversations.chat(
            scope.account_id,
            body.content,
            conversation_id=conversation_id,
            user_id=scope.user.id,
            document_ids=document_ids,
            db=scope.db,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Conversation not found."},
        )

    return MessageAnswerOut(
        message_id=message_id,
        answer=result.answer,
        supported=result.supported,
        citations=[
            CitationOut(
                document_id=c.document_id,
                title=c.title,
                page=c.page,
                fact_id=c.fact_id,
            )
            for c in result.citations
        ],
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
)
def list_messages(
    conversation_id: uuid.UUID,
    scope: AccountScope = Depends(get_current_account),
) -> list[MessageOut]:
    """Return the conversation's full message history, oldest first."""
    convo = scope.db.scalar(
        scope.select(Conversation).where(Conversation.id == conversation_id)
    )
    if convo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Conversation not found."},
        )
    rows = scope.db.scalars(
        select(Message)
        .where(
            Message.account_id == scope.account_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    return [MessageOut.model_validate(m) for m in rows]
