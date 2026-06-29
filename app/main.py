"""FastAPI application entry point.

Wires the app, a standard error envelope, an unauthenticated `/health` probe
(which checks DB connectivity), and one authenticated `/api/v1/me` route that
exercises the auth + account-scoping path end to end.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.core.config import get_settings
from app.core.scoping import AccountScope, get_current_account
from app.db.session import engine

settings = get_settings()

app = FastAPI(title="filemindr", version="0.1.0")

app.include_router(documents_router)
app.include_router(conversations_router)


@app.get("/health", tags=["ops"])
def health() -> JSONResponse:
    """Liveness + DB connectivity. Returns 200 only if `SELECT 1` succeeds."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "env": settings.app_env,
                "database": "down",
                "detail": str(exc.__class__.__name__),
            },
        )
    return JSONResponse(
        content={"status": "ok", "env": settings.app_env, "database": "up"}
    )


@app.get("/api/v1/me", tags=["identity"])
def me(scope: AccountScope = Depends(get_current_account)) -> dict:
    """Return the authenticated user and active account. Gated by auth+scoping."""
    return {
        "user": {"id": str(scope.user.id), "email": scope.user.email},
        "account": {
            "id": str(scope.account.id),
            "type": scope.account.type,
            "name": scope.account.name,
        },
    }
