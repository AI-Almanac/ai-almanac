from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import text

from ai_almanac.settings import settings
from ai_almanac.server.db import get_db
from .chat_state import ChatArtifact, ChatTurn
from .storage import get_storage


def _chat_figure_signature(figure_id: str, user_id: str, expires_at: int) -> str:
    payload = f"{figure_id}:{user_id}:{expires_at}".encode()
    secret = getattr(settings, "chat_figure_signing_secret", "dev-chat-figure-secret")
    digest = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def signed_chat_figure_url(
    figure_id: str, user_id: str, expires_at: int | None = None
) -> str:
    if expires_at is None:
        expires_at = int(datetime.now(timezone.utc).timestamp()) + 3600
    sig = _chat_figure_signature(figure_id, user_id, expires_at)
    return (
        f"/chat/figures/{figure_id}/public?{urlencode({'exp': expires_at, 'sig': sig})}"
    )


def verify_chat_figure_signature(
    figure_id: str, user_id: str, expires_at: int, sig: str
) -> bool:
    now = int(datetime.now(timezone.utc).timestamp())
    if expires_at < now:
        return False
    expected = _chat_figure_signature(figure_id, user_id, expires_at)
    return hmac.compare_digest(expected, sig)


def hydrate_turn_artifact_urls(turn: ChatTurn, user_id: str) -> ChatTurn:
    def hydrate_artifact(artifact: ChatArtifact) -> ChatArtifact:
        signed_url = signed_chat_figure_url(artifact.id, user_id)
        return artifact.model_copy(update={"url": signed_url})

    return turn.model_copy(
        update={
            "artifacts": [hydrate_artifact(artifact) for artifact in turn.artifacts],
            "tool_calls": [
                tool_call.model_copy(
                    update={
                        "artifacts": [
                            hydrate_artifact(artifact)
                            for artifact in tool_call.artifacts
                        ]
                    }
                )
                for tool_call in turn.tool_calls
            ],
        }
    )


async def create_chat_figure_artifact(
    session_id: str,
    user_id: str,
    data: bytes,
    *,
    label: str | None = None,
    filename: str | None = None,
    media_type: str | None = None,
) -> ChatArtifact:
    artifact_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    await asyncio.to_thread(get_storage().save_chat_figure, artifact_id, data)

    async with get_db() as conn:
        await conn.execute(
            text("""
                INSERT INTO chat_artifacts (id, session_id, user_id, kind, storage_key, created_at)
                VALUES (:id, :session_id, :user_id, 'figure', :storage_key, :created_at)
            """),
            {
                "id": artifact_id,
                "session_id": session_id,
                "user_id": user_id,
                "storage_key": artifact_id,
                "created_at": created_at,
            },
        )

    return ChatArtifact(
        id=artifact_id,
        kind="figure",
        url=signed_chat_figure_url(artifact_id, user_id),
        label=label,
        filename=filename,
        media_type=media_type,
        created_at=created_at,
    )


async def delete_chat_figure_artifact(storage_key: str) -> None:
    await asyncio.to_thread(get_storage().delete_chat_figure, storage_key)
