"""Pixabay image search, download to images/, and CDN URL construction."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

from config import CDN_TEMPLATE, IMAGES_DIR, PIXABAY_API_URL, Config
from retry_helper import retry

logger = logging.getLogger(__name__)

PIXABAY_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
MIN_WIDTH = 1200
MIN_HITS = 5
PER_PAGE = 20
MAX_CANDIDATES = 6


@dataclass
class FetchedImage:
    """A downloaded image plus its public CDN URL."""

    local_path: Path
    public_url: str
    alt_text: str
    credit_line: str
    attribution: str = ""

    def html_block(self, extra_alt_note: str = "") -> str:
        """Return the <img> tag plus the Pixabay credit line."""
        alt = self.alt_text if not extra_alt_note else f"{self.alt_text}. {extra_alt_note}"
        return (
            f'<figure style="margin:0 0 1em 0;text-align:center;">'
            f'<img src="{self.public_url}" alt="{alt}" '
            f'style="max-width:100%;height:auto;border-radius:6px;" />'
            f'<figcaption style="font-size:0.85em;color:#777;margin-top:0.4em;">'
            f"Image: Pixabay{self.credit_line}</figcaption>"
            f"</figure>"
        )


def _search_pixabay(api_key: str, keyword: str) -> list[dict]:
    params = urlencode(
        {
            "key": api_key,
            "q": keyword,
            "image_type": "photo",
            "safesearch": "true",
            "orientation": "horizontal",
            "per_page": PER_PAGE,
        }
    )
    url = f"{PIXABAY_API_URL}?{params}"

    response = requests.get(url, timeout=PIXABAY_TIMEOUT, headers={"User-Agent": "blogger-agent/1.0"})
    if response.status_code == 429:
        logger.warning("Pixabay rate limited (429), will retry with backoff")
        raise requests.exceptions.HTTPError("429 Too Many Requests", response=response)
    response.raise_for_status()
    data = response.json()
    hits = data.get("hits") or []
    logger.info("Pixabay search '%s' returned %d hits", keyword, len(hits))
    return hits


def _image_width(hit: dict) -> int:
    return int(hit.get("imageWidth") or 0)


def _image_height(hit: dict) -> int:
    return int(hit.get("imageHeight") or 0)


def _best_url(hit: dict) -> str | None:
    return hit.get("largeImageURL") or hit.get("webformatURL") or None


def _rank_hits(hits: list[dict]) -> list[dict]:
    """Order usable photos from best to worst."""
    candidates = [
        hit
        for hit in hits
        if _best_url(hit)
        and _image_width(hit) >= MIN_WIDTH
        and not hit.get("isLowQuality")
    ]
    candidates.sort(
        key=lambda hit: (
            _image_width(hit) * _image_height(hit),
            _image_width(hit),
        ),
        reverse=True,
    )
    return candidates


def _download(hit: dict, destination: Path) -> None:
    """Download one photo, preferring the 1280px file and falling back to 640px."""
    urls = [url for url in (hit.get("largeImageURL"), hit.get("webformatURL")) if url]
    if not urls:
        raise ValueError("Pixabay hit has no download URL")

    errors: list[str] = []
    for url in urls:
        response = requests.get(
            url, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": "blogger-agent/1.0"}
        )
        if response.status_code == 200 and len(response.content) >= 5000:
            destination.write_bytes(response.content)
            return
        errors.append(f"http={response.status_code} bytes={len(response.content)}")
        time.sleep(1.5)

    raise RuntimeError("all Pixabay download URLs failed: " + ", ".join(errors))


def fetch_image(cfg: Config, keyword: str, alt_text: str, date_str: str) -> FetchedImage | None:
    """Search Pixabay and download the best match. Returns None if nothing usable."""
    if not cfg.pixabay_api_key:
        logger.warning("PIXABAY_API_KEY missing, skipping image")
        return None

    # Validate GITHUB_REPOSITORY
    repo = cfg.github_repository.strip()
    if not repo or "=" in repo or " " in repo:
        raise RuntimeError(f"Invalid GITHUB_REPOSITORY: '{repo}'. Must be in format 'owner/repo' with no spaces or '='")

    try:
        hits = retry(
            lambda: _search_pixabay(cfg.pixabay_api_key, keyword),
            attempts=5,
            exceptions=(requests.exceptions.RequestException, Exception),
            label="pixabay-search",
        )
        candidates = _rank_hits(hits)
        if not candidates:
            logger.warning("No usable Pixabay image for keyword '%s'", keyword)
            return None

        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        # Create unique filename per post (date plus short hash)
        hash_input = f"{date_str}-{keyword}-{time.time()}"
        short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        filename = f"{date_str}-{short_hash}.jpg"
        destination = IMAGES_DIR / filename

        best: dict | None = None
        for hit in candidates[:MAX_CANDIDATES]:
            try:
                retry(
                    lambda hit=hit: _download(hit, destination),
                    attempts=3,
                    exceptions=(Exception,),
                    label="pixabay-download",
                )
            except Exception as exc:  # noqa: PERF203 - try the next candidate
                logger.warning("Candidate image failed (%s), trying next", exc)
                continue
            best = hit
            break

        if best is None:
            logger.warning("All %d Pixabay candidates failed to download", min(MAX_CANDIDATES, len(candidates)))
            return None

        public_url = CDN_TEMPLATE.format(repo=repo, filename=filename)

        author = (best.get("user") or "").strip()
        credit_line = f" ({author} on Pixabay)" if author else ""
        logger.info("Saved image %s (%d bytes)", destination, destination.stat().st_size)

        return FetchedImage(
            local_path=destination,
            public_url=public_url,
            alt_text=alt_text,
            credit_line=credit_line,
            attribution=best.get("pageURL", ""),
        )
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - image failure must not kill the post
        logger.error("Image fetch failed (post will publish without image): %s", exc)
        return None


def build_alt_text(title: str, keyword: str) -> str:
    base = title.strip().rstrip(".")
    return f"{base} - illustration for {keyword.strip()}".strip()[:150]