"""Blogger API v3 publishing via a stored OAuth2 refresh token."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient import discovery, errors

from config import BLOGGER_SCOPE, Config
from retry_helper import retry

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://www.googleapis.com/discovery/v1/apis/blogger/v3/rest"


@dataclass
class PublishedPost:
    post_id: str
    url: str
    is_draft: bool


def build_credentials(cfg: Config) -> Credentials:
    """Build Credentials from the stored refresh token with auto-refresh."""
    creds = Credentials(
        token=None,
        refresh_token=cfg.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
        scopes=[BLOGGER_SCOPE],
    )
    creds.refresh(Request())
    logger.info("Google credentials refreshed successfully")
    return creds


def build_service(cfg: Config):
    creds = build_credentials(cfg)
    return discovery.build("blogger", "v3", credentials=creds, cache_discovery=False)


def publish_post(
    cfg: Config,
    *,
    title: str,
    html_body: str,
    labels: list[str],
    meta_description: str = "",
) -> PublishedPost:
    """Insert the post into the configured blog. Returns the post id and URL."""
    if not title.strip() or not html_body.strip():
        raise ValueError("Refusing to publish an empty post")

    service = retry(
        lambda: build_service(cfg),
        attempts=3,
        exceptions=(errors.HttpError, Exception),
        label="blogger-auth",
    )

    body = {
        "kind": "blogger#post",
        "blog": {"id": cfg.blog_id},
        "title": title,
        "content": html_body,
        "labels": labels,
    }
    if meta_description:
        body["description"] = meta_description

    def _insert() -> dict:
        result = service.posts().insert(
            blogId=cfg.blog_id,
            body=body,
            isDraft=cfg.should_draft,
            fields="id,url,status,title,labels",
        ).execute()
        if not result.get("id"):
            raise RuntimeError("Blogger returned no post id")
        return result

    result = retry(_insert, attempts=3, exceptions=(Exception,), label="blogger-insert")

    is_draft = result.get("status") == "DRAFT"
    if is_draft:
        # Blogger returns the blog homepage for drafts, which is not useful.
        edit_url = f"https://draft.blogger.com/post-edit/{cfg.blog_id}/{result['id']}"
        url = edit_url
    else:
        url = result.get("url") or (
            f"https://draft.blogger.com/post-edit/{cfg.blog_id}/{result['id']}"
        )
    state = "DRAFT" if is_draft else "PUBLISHED"
    logger.info("Post %s -> %s (%s)", result["id"], url, state)

    return PublishedPost(post_id=result["id"], url=url, is_draft=is_draft)


def list_recent_titles(cfg: Config, count: int = 20) -> list[str]:
    """Used by auth_setup / debugging to confirm the token works."""
    service = build_service(cfg)
    result = service.posts().list(blogId=cfg.blog_id, maxResults=count, fields="items(title)").execute()
    return [item.get("title", "") for item in result.get("items", [])]


__all__ = [
    "PublishedPost",
    "build_credentials",
    "build_service",
    "publish_post",
    "list_recent_titles",
]