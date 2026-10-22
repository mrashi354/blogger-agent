"""Safe linking for blog posts.

Builds the link sections appended to each post and strips out any URL the LLM
invented that is not on the allow-list.

Link sources:
- internal   : previously published (non-draft, real post) entries in posted_log.json
- own site   : SITE_URL / SITE_NAME from config (optional)
- external   : hand-written official sources from external_sources.json

The LLM never chooses URLs. Everything here is deterministic and allow-listed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from config import EXTERNAL_SOURCES_FILE, Config, POSTED_LOG_FILE

logger = logging.getLogger(__name__)

MAX_INTERNAL_LINKS = 3
MAX_EXTERNAL_LINKS = 2
LINK_CHECK_TIMEOUT = 10
INTERNAL_HEADING = "Ye bhi padhein"
OFFICIAL_HEADING = "Official sources"
AUTHOR_BOX_LABEL = "Mere baare mein / more tools"

_USER_AGENT = "blogger-agent/1.0"


# --------------------------------------------------------------------------- #
# Data holders
# --------------------------------------------------------------------------- #
@dataclass
class Link:
    url: str
    title: str
    kind: str  # "internal" | "site" | "external"


@dataclass
class LinkPlan:
    """All links selected for one post, in the order they are rendered."""

    internal: list[Link] = field(default_factory=list)
    site: Link | None = None
    external: list[Link] = field(default_factory=list)

    def all_links(self) -> list[Link]:
        links = list(self.internal)
        if self.site is not None:
            links.append(self.site)
        links.extend(self.external)
        return links

    def allowed_urls(self) -> set[str]:
        return {link.url for link in self.all_links()}


# --------------------------------------------------------------------------- #
# posted_log.json helpers
# --------------------------------------------------------------------------- #
def load_posted_log() -> list[dict[str, Any]]:
    if not POSTED_LOG_FILE.exists():
        return []
    try:
        with POSTED_LOG_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read posted_log.json for internal links: %s", exc)
        return []
    entries = data.get("posts", []) if isinstance(data, dict) else data
    return [entry for entry in entries if isinstance(entry, dict)]


def _is_real_post_url(url: str) -> bool:
    """A real post URL, not the blog homepage or an empty/relative link."""
    if not url or not url.strip():
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if path.startswith("post-edit"):
        return False
    return True


def _is_eligible(entry: dict[str, Any]) -> bool:
    """Only published (non-draft) entries whose url is a real post URL."""
    if entry.get("is_draft") is True or entry.get("draft") is True:
        return False
    if str(entry.get("status", "")).upper() == "DRAFT":
        return False
    return _is_real_post_url(str(entry.get("url", "")))


_STOPWORDS = {
    "how", "to", "the", "a", "an", "of", "in", "on", "for", "and", "or",
    "your", "you", "is", "it", "at", "with", "without", "actually", "when",
    "ka", "ke", "ki", "hai", "kare", "karein", "se", "me", "mein", "aur",
}


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _relevance(entry: dict[str, Any], labels: set[str], topic_words: set[str]) -> int:
    entry_labels = {str(label).strip().lower() for label in entry.get("labels", []) or []}
    label_hits = len(entry_labels & labels)
    title_words = _keywords(str(entry.get("title", "")) + " " + str(entry.get("topic", "")))
    word_hits = len(title_words & topic_words)
    return label_hits * 2 + word_hits


def pick_internal_links(
    posted: list[dict[str, Any]],
    *,
    labels: list[str],
    title: str,
    topic: str = "",
    limit: int = MAX_INTERNAL_LINKS,
) -> list[Link]:
    """Up to `limit` most related previously published posts."""
    label_set = {str(label).strip().lower() for label in labels}
    topic_words = _keywords(f"{title} {topic}")

    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in posted:
        if not _is_eligible(entry):
            continue
        score = _relevance(entry, label_set, topic_words)
        if score <= 0:
            continue
        scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)

    links: list[Link] = []
    seen: set[str] = set()
    for _, entry in scored:
        url = str(entry.get("url", "")).strip()
        if url in seen:
            continue
        seen.add(url)
        links.append(Link(url=url, title=str(entry.get("title", "")).strip(), kind="internal"))
        if len(links) >= limit:
            break
    return links


# --------------------------------------------------------------------------- #
# External sources
# --------------------------------------------------------------------------- #
def load_external_sources() -> list[dict[str, Any]]:
    if not EXTERNAL_SOURCES_FILE.exists():
        return []
    try:
        with EXTERNAL_SOURCES_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read external_sources.json: %s", exc)
        return []
    sources = data.get("sources", []) if isinstance(data, dict) else data
    return [s for s in sources if isinstance(s, dict) and s.get("url") and s.get("title")]


def _url_is_live(url: str) -> bool:
    """HEAD request, falling back to GET, 200 only. Never raises."""
    for method in ("head", "get"):
        try:
            response = requests.request(
                method, url, timeout=LINK_CHECK_TIMEOUT,
                allow_redirects=True, headers={"User-Agent": _USER_AGENT},
            )
            if response.status_code == 200:
                return True
            if method == "head" and response.status_code in (403, 405, 501):
                continue
        except requests.RequestException as exc:
            logger.debug("Link check failed (%s) for %s: %s", method, url, exc)
    return False


def pick_external_links(
    topic: str,
    *,
    limit: int = MAX_EXTERNAL_LINKS,
    checker=_url_is_live,
) -> list[Link]:
    """Up to `limit` official sources whose keywords match the topic."""
    topic_words = _keywords(topic)
    if not topic_words:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for source in load_external_sources():
        keywords = {str(k).strip().lower() for k in source.get("keywords", []) or []}
        hits = len(keywords & topic_words)
        if hits > 0:
            scored.append((hits, source))

    scored.sort(key=lambda item: item[0], reverse=True)

    links: list[Link] = []
    for _, source in scored:
        url = str(source["url"]).strip()
        if not _url_is_live(url):
            logger.info("Dropping external source (not 200): %s", url)
            continue
        links.append(Link(url=url, title=str(source["title"]).strip(), kind="external"))
        if len(links) >= limit:
            break
    return links


# --------------------------------------------------------------------------- #
# Plan + rendering
# --------------------------------------------------------------------------- #
def build_link_plan(
    cfg: Config,
    *,
    posted: list[dict[str, Any]],
    labels: list[str],
    title: str,
    topic: str = "",
    check_external: bool = True,
) -> LinkPlan:
    plan = LinkPlan()
    plan.internal = pick_internal_links(posted, labels=labels, title=title, topic=topic)

    if cfg.site_url and cfg.site_name:
        plan.site = Link(url=cfg.site_url.strip(), title=cfg.site_name.strip(), kind="site")

    if check_external:
        plan.external = pick_external_links(topic)
    return plan


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_link_sections(plan: LinkPlan) -> str:
    """HTML for the appended link sections, or '' when there is nothing to add."""
    blocks: list[str] = []

    if plan.internal:
        items = "".join(
            f'<li><a href="{link.url}">{_escape(link.title)}</a></li>'
            for link in plan.internal
        )
        blocks.append(f"<h2>{INTERNAL_HEADING}</h2>\n<ul>{items}</ul>")

    if plan.external:
        anchors = " ".join(
            f'<a href="{link.url}" target="_blank" rel="noopener">{_escape(link.title)}</a>'
            for link in plan.external
        )
        blocks.append(f"<p><strong>{OFFICIAL_HEADING}:</strong> {anchors}</p>")

    if plan.site is not None:
        blocks.append(
            f'<p>{AUTHOR_BOX_LABEL}: '
            f'<a href="{plan.site.url}">{_escape(plan.site.title)}</a></p>'
        )

    return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# URL sanitizer
# --------------------------------------------------------------------------- #
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*(?P<quote>[\"'])(?P<url>.*?)(?P=quote)[^>]*>(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_HREF_RE = re.compile(
    r"\bhref\s*=\s*(?P<quote>[\"'])(?P<url>.*?)(?P=quote)", re.IGNORECASE
)


def sanitize_body_links(
    html_body: str,
    allowed_urls: set[str],
    *,
    extra_allowed: list[str] | None = None,
) -> str:
    """Unwrap any link whose target is not on the allow-list, keeping its text."""
    allowed = set(allowed_urls)
    allowed.update(extra_allowed or [])

    def normalize(url: str) -> str:
        cleaned = url.strip()
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    allowed_norm = {normalize(url) for url in allowed if url}

    def replace_anchor(match: re.Match) -> str:
        url = match.group("url").strip()
        if not url:
            return match.group("text")
        norm = normalize(url)
        if norm in allowed_norm or url in allowed:
            return match.group(0)
        logger.info("Stripping disallowed LLM link: %s", url)
        return match.group("text")

    sanitized = _ANCHOR_RE.sub(replace_anchor, html_body)

    # Any leftover href="" that is not part of an <a> (e.g. bare/duplicate) is dropped.
    def replace_href(match: re.Match) -> str:
        url = match.group("url").strip()
        if not url or normalize(url) in allowed_norm:
            return match.group(0)
        return 'data-removed-href=""'

    return _HREF_RE.sub(replace_href, sanitized)


def collect_extra_allowed(image) -> list[str]:
    """The Pixabay image URL (and its page URL) is always allowed."""
    urls: list[str] = []
    if image is not None:
        if getattr(image, "public_url", ""):
            urls.append(image.public_url)
        if getattr(image, "attribution", ""):
            urls.append(image.attribution)
    return urls
