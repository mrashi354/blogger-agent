"""Daily Blogger post agent.

Pipeline: pick topic -> generate post (LLM) -> fetch image (Pixabay)
-> publish (Blogger) -> update posted_log.json

Usage:
    python main.py --dry-run     # generate + print, no publish, no log write
    python main.py              # publish (DRAFT_MODE decides draft vs live)
    python main.py --draft      # force draft regardless of DRAFT_MODE
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as config_module
from config import LOGS_DIR, POSTED_LOG_FILE, PROJECT_ROOT, TOPICS_FILE, Config, load_config
from content_generator import ContentGenerator
from image_handler import build_alt_text, fetch_image

logger = logging.getLogger("main")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def setup_logging(dry_run: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"{stamp}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    if dry_run:
        # Keep local logs/ clean during dry runs so the git diff stays empty.
        logger.info("Dry run: file logging disabled")
        return

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logger.info("Logging to %s", log_path)


# --------------------------------------------------------------------------- #
# Topic selection
# --------------------------------------------------------------------------- #
def load_topics(cfg: Config) -> list[str]:
    with TOPICS_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    topics = [str(t).strip() for t in data.get("topics", []) if str(t).strip()]
    if not topics:
        raise RuntimeError(f"No topics found in {TOPICS_FILE}")
    logger.info("Loaded %d topic ideas from topics.json", len(topics))
    return topics


def load_posted_log() -> list[dict[str, Any]]:
    if not POSTED_LOG_FILE.exists():
        return []
    with POSTED_LOG_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("posts", []) if isinstance(data, dict) else data
    return [entry for entry in entries if isinstance(entry, dict)]


def _similar(a: str, b: str) -> bool:
    """Cheap near-duplicate check on topic/title text."""
    stopwords = {
        "how", "to", "the", "a", "an", "of", "in", "on", "for", "and", "or",
        "your", "you", "is", "it", "at", "with", "without", "actually", "when",
    }

    def tokens(text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {w for w in words if w not in stopwords and len(w) > 2}

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def select_topic(topics: list[str], posted: list[dict[str, Any]]) -> str:
    """Pick the first unused topic that is not near-duplicate of past entries."""
    used_topics = [str(entry.get("topic", "")) for entry in posted]
    used_titles = [str(entry.get("title", "")) for entry in posted]

    fresh = [t for t in topics if t not in used_topics]
    if not fresh:
        raise RuntimeError(
            "Every topic in topics.json has already been posted. Add more topics."
        )

    for topic in fresh:
        clash = next(
            (
                used
                for used in (used_topics + used_titles)
                if used and _similar(topic, used)
            ),
            None,
        )
        if clash is None:
            logger.info("Selected topic: %s", topic)
            return topic

    logger.warning("No topic passed the duplicate check, using first unused: %s", fresh[0])
    return fresh[0]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_post(post, image) -> str:
    """Combine image block (if any) with the generated HTML body."""
    body = post.html_body
    if image is not None:
        block = image.html_block()
        body = f"{block}\n{body}"
    return body


def save_preview(title: str, html: str, date_str: str) -> Path:
    preview_dir = PROJECT_ROOT / "logs" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    path = preview_dir / f"{date_str}.html"
    counter = 2
    while path.exists():
        path = preview_dir / f"{date_str}-{counter}.html"
        counter += 1
    path.write_text(
        f"<!-- DRY RUN PREVIEW: {date_str} -->\n"
        f"<h1>{title}</h1>\n<hr>\n{html}\n",
        encoding="utf-8",
    )
    return path


def plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", "\n", html)
    return re.sub(r"\n{2,}", "\n", text).strip()


def print_preview(post, html: str, image, date_str: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nDRY RUN PREVIEW - nothing was published or written\n{bar}")
    print(f"Topic        : {post.topic}")
    print(f"Title        : {post.title}")
    print(f"Labels       : {', '.join(post.labels)}")
    print(f"Keyword      : {post.search_keyword}")
    print(f"Meta desc    : {post.meta_description}")
    print(f"Image        : {image.public_url if image else 'none (post will have no image)'}")
    print(f"HTML length  : {len(html)} chars")
    words = len(plain_text(html).split())
    print(f"Word count   : ~{words}")
    print(f"\n{bar}\nBODY\n{bar}")
    body_text = plain_text(html)
    print(body_text[:4000])
    if len(body_text) > 4000:
        print("\n... [truncated] ...\n")
    print(bar)


# --------------------------------------------------------------------------- #
# posted_log.json
# --------------------------------------------------------------------------- #
def append_posted_log(date_str: str, topic: str, title: str, url: str, labels: list[str]) -> None:
    entries = load_posted_log()
    entries.append(
        {
            "date": date_str,
            "topic": topic,
            "title": title,
            "url": url,
            "labels": labels,
        }
    )
    payload = {"posts": entries}
    with POSTED_LOG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    logger.info("posted_log.json updated (%d total posts)", len(entries))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(dry_run: bool, force_draft: bool, push_image_first: bool, topic: str | None = None) -> int:
    setup_logging(dry_run)
    cfg = load_config(require_all=not dry_run)
    if force_draft:
        cfg = cfg.with_draft(True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting daily post run (date=%s, dry_run=%s, draft=%s)", date_str, dry_run, cfg.should_draft)

    posted = load_posted_log()
    topics = load_topics(cfg)
    if topic:
        if topic not in topics:
            raise RuntimeError(f"Topic not found in {TOPICS_FILE.name}: {topic}")
        logger.info("Using requested topic: %s", topic)
    else:
        topic = select_topic(topics, posted)

    logger.info("Step 1/5: generating content via LLM...")
    generator = ContentGenerator(cfg)
    post = generator.generate(topic, used_titles=[str(e.get("title", "")) for e in posted],
                              used_topics=[str(e.get("topic", "")) for e in posted])
    logger.info("Generated: '%s' (%d labels)", post.title, len(post.labels))

    logger.info("Step 2/5: fetching image via Pixabay...")
    alt = build_alt_text(post.title, post.search_keyword)
    image = fetch_image(cfg, post.search_keyword, alt, date_str)
    logger.info("Image: %s", image.public_url if image else "not found")

    html = render_post(post, image)

    if dry_run:
        logger.info("Step 3/5: dry run, skipping publish and log write")
        print_preview(post, html, image, date_str)
        preview = save_preview(post.title, html, date_str)
        print(f"Preview saved to: {preview}")
        logger.info("Dry run complete")
        return 0

    cfg.require_publish_credentials()

    if push_image_first and image is not None:
        logger.info("Step 3/5: pushing image to git so the CDN link resolves...")
        from git_sync import push_image as git_push_image

        git_push_image(date_str)

    logger.info("Step 4/6: publishing to Blogger (draft=%s)...", cfg.should_draft)
    from blogger_client import publish_post  # imported late so dry runs need no google libs

    published = publish_post(
        cfg,
        title=post.title,
        html_body=html,
        labels=post.labels,
        meta_description=post.meta_description,
    )

    logger.info("Step 5/6: publish done -> %s", published.url)
    logger.info("Step 6/6: recording in posted_log.json")
    append_posted_log(date_str, topic, post.title, published.url, post.labels)

    print("\n" + "=" * 78)
    print(f"{'DRAFT' if published.is_draft else 'PUBLISHED'}: {post.title}")
    print(f"URL     : {published.url}")
    print(f"Labels  : {', '.join(post.labels)}")
    print(f"Image   : {image.public_url if image else 'none'}")
    print("=" * 78 + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish one blog post per day to Blogger.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and print only. No publish, no log write.")
    parser.add_argument("--draft", action="store_true", help="Force draft mode for this run.")
    parser.add_argument(
        "--push-image-first",
        action="store_true",
        help="Git commit and push today's image before publishing (used by CI).",
    )
    parser.add_argument(
        "--topic",
        help="Use this exact topic from topics.json instead of the next unused one.",
    )
    args = parser.parse_args()

    try:
        return run(
            dry_run=args.dry_run,
            force_draft=args.draft,
            push_image_first=args.push_image_first,
            topic=args.topic,
        )
    except KeyboardInterrupt:
        logger.error("Interrupted by user")
        return 130
    except Exception as exc:  # noqa: BLE001 - top level guard, must exit non-zero
        logger.error("Run failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())