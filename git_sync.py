"""Git helpers used by CI to push the image before the post goes live.

The post HTML references the image via
https://cdn.jsdelivr.net/gh/{repo}@main/images/{file}.
That URL only resolves once the image is committed and pushed to the
main branch, so in CI we push the image first, wait for jsDelivr to pick
it up, and only then publish.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from config import IMAGES_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def push_image(date_str: str, *, wait_seconds: int = 10) -> bool:
    """Commit and push today's image. Returns True if something was pushed."""
    # Find image matching today's date (could have hash suffix)
    image_files = list(IMAGES_DIR.glob(f"{date_str}*.jpg"))
    if not image_files:
        logger.warning("No image for date %s in %s, nothing to push", date_str, IMAGES_DIR)
        return False
    
    # Use the most recent one
    image_path = sorted(image_files)[-1]
    
    _git("add", str(Path("images") / image_path.name))
    staged = _git("diff", "--cached", "--name-only")
    if not staged:
        logger.info("Image already committed, skipping push")
        return False

    _git("commit", "-m", f"chore: add post image {date_str}")
    _git("push")
    logger.info("Pushed %s to main; waiting %ds for CDN propagation", image_path.name, wait_seconds)
    time.sleep(wait_seconds)
    return True