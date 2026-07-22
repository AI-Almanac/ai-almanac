"""User feedback — forwarded to GitHub issues.

Feedback submissions from the SPA are turned into labeled issues on the
project repository. Configuration is environment-only (no settings.py knobs):

- ``FEEDBACK_GITHUB_TOKEN``: token with issue-write access. Feedback is
  disabled when unset.
- ``FEEDBACK_GITHUB_REPO``: ``owner/name`` target repo
  (default ``AI-Almanac/ai-almanac``).

There is no local persistence by design: GitHub is the single sink, and the
client surfaces delivery failures to the user.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_almanac.server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

_DEFAULT_REPO = "AI-Almanac/ai-almanac"
_GITHUB_API = "https://api.github.com"
# GitHub caps issue bodies at 65536 characters; leave headroom.
_MAX_BODY_CHARS = 60_000
_MAX_BREADCRUMBS = 150


def feedback_enabled() -> bool:
    return bool(os.environ.get("FEEDBACK_GITHUB_TOKEN", "").strip())


def _target_repo() -> str:
    return os.environ.get("FEEDBACK_GITHUB_REPO", "").strip() or _DEFAULT_REPO


class FeedbackSubmission(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    category: Literal["bug", "idea", "other"] = "other"
    page: str = Field(default="", max_length=500)
    # Point-in-time client snapshot (route, versions, viewport, account mode…).
    snapshot: dict = Field(default_factory=dict)
    # Recent activity trail from the client's breadcrumb ring buffer.
    breadcrumbs: list[dict] = Field(default_factory=list, max_length=_MAX_BREADCRUMBS)


class FeedbackResult(BaseModel):
    issue_url: str


def _github_client() -> httpx.AsyncClient:
    """Seam for tests; the app's own test client is also httpx-based, so tests
    patch this instead of ``httpx.AsyncClient`` globally."""
    return httpx.AsyncClient(timeout=10.0)


def _issue_title(body: FeedbackSubmission) -> str:
    first_line = body.message.strip().splitlines()[0]
    if len(first_line) > 80:
        first_line = first_line[:77] + "…"
    return f"[{body.category}] {first_line}"


def _issue_body(body: FeedbackSubmission, user_label: str) -> str:
    snapshot_json = json.dumps(body.snapshot, indent=2, default=str)
    breadcrumbs_json = json.dumps(body.breadcrumbs, indent=2, default=str)
    text = (
        f"{body.message.strip()}\n\n"
        f"---\n\n"
        f"**Submitted by:** {user_label}\n"
        f"**Page:** {body.page or 'unknown'}\n\n"
        f"<details><summary>Context snapshot</summary>\n\n"
        f"```json\n{snapshot_json}\n```\n\n</details>\n\n"
        f"<details><summary>Breadcrumb trail ({len(body.breadcrumbs)} events)</summary>\n\n"
        f"```json\n{breadcrumbs_json}\n```\n\n</details>\n"
    )
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS] + "\n… (truncated)"
    return text


@router.post("", response_model=FeedbackResult)
async def submit_feedback(body: FeedbackSubmission, user: CurrentUser) -> FeedbackResult:
    token = os.environ.get("FEEDBACK_GITHUB_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Feedback is not configured on this deployment.",
        )

    user_label = user.email or user.subject or user.id
    payload = {
        "title": _issue_title(body),
        "body": _issue_body(body, user_label),
        "labels": ["demo-feedback", f"feedback:{body.category}"],
    }

    try:
        async with _github_client() as client:
            res = await client.post(
                f"{_GITHUB_API}/repos/{_target_repo()}/issues",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as e:
        logger.error("feedback: GitHub unreachable: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Could not reach GitHub to record feedback. Please try again.",
        ) from e

    if res.status_code != 201:
        logger.error(
            "feedback: GitHub issue creation failed (%d): %s",
            res.status_code,
            res.text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail="GitHub rejected the feedback submission. Please try again.",
        )

    issue_url = res.json().get("html_url", "")
    logger.info("feedback: created issue %s for %s", issue_url, user_label)
    return FeedbackResult(issue_url=issue_url)
