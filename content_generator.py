"""LLM content generation with forced JSON output and validation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import BadRequestError, OpenAI

from config import Config
from retry_helper import retry

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("title", "html_body", "labels", "search_keyword", "meta_description")
MIN_LABELS = 3
MAX_LABELS = 5
MIN_HARD_FLOOR = 300
LENGTH_ATTEMPTS = 3
FORBIDDEN_TAGS = ("<html", "<body", "<!doctype", "<head")
DISCLAIMER_LINE = "Ye general jaankari hai, financial advice nahi"


SYSTEM_PROMPT = """You are a senior blog writer for a personal finance blog.

Hard rules you must always follow:
- Write original, genuinely helpful content for a general reader. Never invent
  statistics, studies, surveys, expert quotes, regulations, laws, percentages or numbers presented
  as facts. If a figure is needed and you are not certain, write the guidance
  qualitatively instead of inventing a number. Keep financial advice general.
- No keyword stuffing. Use natural language; the main topic should appear only
  where it reads naturally.
- You must not copy text from anywhere; write it fresh.

Output format:
- Respond with a single raw JSON object and nothing else. No markdown fences,
  no commentary before or after.
- Keys:
  "title"                 : SEO-friendly title, under 70 characters.
  "html_body"             : the full post as clean HTML fragment.
  "labels"                : array of 3 to 5 short lowercase tags, no "#".
  "search_keyword"        : 2-4 words, good for finding a royalty-free image.
  "meta_description"      : 140-160 characters, plain text, no quotes.

HTML rules for html_body:
- Output PURE HTML only. NEVER use markdown. Do NOT use **, ##, or any markdown characters.
- A fragment only. No <html>, <head>, <body>, <!DOCTYPE> or inline CSS.
- Start with 2-3 short sentences as an intro paragraph.
- Then 3 to 5 <h2> sections; use <h3> subsections only if a section is long.
- Write every heading in {language}, exactly like the body text. Never leave a
  heading in English when the body is not English.
- Use <ul><li><strong>..</strong></li></ul> for lists where they genuinely help. Always keep a space between words and after closing tags.
- End with an <h2> conclusion plus a short closing paragraph, followed by the exact one-line disclaimer: Ye general jaankari hai, financial advice nahi
- Use <strong> sparingly for emphasis.
- Roughly {min_words} to {max_words} words total. Aim for the upper half of that
  range so the post never falls short of {min_words} words.
- No <script>, no iframes, no external CSS or JS."""


@dataclass
class GeneratedPost:
    title: str
    html_body: str
    labels: list[str]
    search_keyword: str
    meta_description: str
    topic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "html_body": self.html_body,
            "labels": self.labels,
            "search_keyword": self.search_keyword,
            "meta_description": self.meta_description,
        }


class ContentValidationError(ValueError):
    """Raised when the model returns JSON that does not meet the schema."""


def word_count(html: str) -> int:
    plain_text = re.sub(r"<[^>]+>", " ", html)
    return len([w for w in re.split(r"\s+", plain_text) if w.strip()])


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _sanitize_model_output(raw: str) -> str:
    """Sanitize raw model output to be more JSON-tolerant."""
    cleaned = _strip_code_fences(raw)
    # Try to extract JSON object if there's extra text
    if not cleaned.strip().startswith("{"):
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
    # Fix common escape issues
    cleaned = cleaned.replace('\\n', '\\n')  # keep as is but be defensive
    return cleaned.strip()


def _strip_markdown_from_html(html_body: str) -> str:
    """Strip markdown artifacts like ** and ## from HTML."""
    # Remove ** markdown bold
    html_body = re.sub(r'\*\*([^*]+)\*\*', r'\1', html_body)
    html_body = re.sub(r'\*([^*]+)\*', r'\1', html_body)
    # Remove ## headings that might leak as markdown
    html_body = re.sub(r'^#{1,6}\s+.*$', '', html_body, flags=re.M)
    # Clean up extra spaces
    html_body = re.sub(r'\s+', ' ', html_body)
    return html_body.strip()


def validate_payload(payload: Any) -> GeneratedPost:
    """Validate the raw JSON object from the model and return a GeneratedPost."""
    if not isinstance(payload, dict):
        raise ContentValidationError(f"Expected a JSON object, got {type(payload).__name__}")

    # Ensure all required keys are present
    missing = [key for key in REQUIRED_KEYS if key not in payload or payload[key] is None]
    if missing:
        raise ContentValidationError(f"Missing keys: {', '.join(missing)}")

    title = str(payload["title"]).strip()
    html_body = str(payload["html_body"]).strip()
    meta_description = str(payload["meta_description"]).strip()
    search_keyword = str(payload["search_keyword"]).strip()

    # Strip markdown artifacts
    html_body = _strip_markdown_from_html(html_body)
    title = title.replace('**', '').replace('##', '').strip()
    meta_description = meta_description.replace('**', '').replace('##', '').strip()
    search_keyword = search_keyword.replace('**', '').replace('##', '').strip()

    # Check for leftover markdown
    if '**' in html_body or '##' in html_body:
        raise ContentValidationError("html_body contains markdown artifacts (** or ##)")

    if not title:
        raise ContentValidationError("title is empty")
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    if not search_keyword:
        raise ContentValidationError("search_keyword is empty")
    if not meta_description:
        raise ContentValidationError("meta_description is empty")

    lower_body = html_body.lower()
    for tag in FORBIDDEN_TAGS:
        if tag in lower_body:
            raise ContentValidationError(f"html_body contains forbidden tag '{tag}'")

    plain_text = re.sub(r"<[^>]+>", " ", html_body)
    words = len([w for w in re.split(r"\s+", plain_text) if w.strip()])
    if words < MIN_HARD_FLOOR:
        raise ContentValidationError(f"html_body is too short ({words} words)")

    if not re.search(r"<h2", lower_body):
        raise ContentValidationError("html_body has no <h2> headings")
    if not re.search(r"<p[>\s]", lower_body):
        raise ContentValidationError("html_body has no paragraphs")

    raw_labels = payload["labels"]
    if not isinstance(raw_labels, list):
        raise ContentValidationError("labels must be a list")
    labels = [str(label).strip().lstrip("#").lower() for label in raw_labels if str(label).strip()]
    labels = list(dict.fromkeys(labels))[:MAX_LABELS]
    if len(labels) < MIN_LABELS:
        raise ContentValidationError(f"Need {MIN_LABELS}-{MAX_LABELS} labels, got {len(labels)}")

    if not (50 <= len(meta_description) <= 200):
        raise ContentValidationError(
            f"meta_description length {len(meta_description)} out of range"
        )

    # Ensure space after closing tags and between words
    html_body = re.sub(r'>\s*<', '> <', html_body)
    html_body = re.sub(r'\s+', ' ', html_body)

    # Add disclaimer if not present
    if DISCLAIMER_LINE not in html_body:
        html_body = html_body.rstrip() + f" <p>{DISCLAIMER_LINE}</p>"

    return GeneratedPost(
        title=title,
        html_body=html_body,
        labels=labels,
        search_keyword=search_keyword,
        meta_description=meta_description,
    )


def build_user_prompt(
    topic: str,
    *,
    niche: str,
    language: str,
    tone: str,
    min_words: int,
    max_words: int,
    used_titles: list[str],
    used_topics: list[str],
) -> str:
    avoid = "\n".join(f"- {item}" for item in (used_titles + used_topics)[:40]) or "- (nothing yet)"
    return f"""Write today's blog post.

Niche: {niche}
Topic for today: {topic}
Language: {language} (write the whole post in this language, keeping HTML tags in English)
Tone: {tone}
Target length: between {min_words} and {max_words} words.

Angles and titles already covered on this blog, DO NOT repeat them:
{avoid}

Today's angle must be clearly different from everything above.
Return only the JSON object described in the system prompt."""


class ContentGenerator:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)
        self.model = cfg.llm_model

    def _chat(self, user_prompt: str, *, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        min_words=self.cfg.min_words,
                        max_words=self.cfg.max_words,
                        language=self.cfg.blog_language,
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.85,
            "max_tokens": 4096,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**payload)
        return response.choices[0].message.content or ""

    def _call_model(self, user_prompt: str) -> GeneratedPost:
        try:
            raw = self._chat(user_prompt, json_mode=True)
        except BadRequestError as exc:
            message = str(exc).lower()
            if "json" not in message and "response_format" not in message:
                raise
            logger.warning("Provider rejected JSON mode, retrying without it")
            raw = self._chat(user_prompt, json_mode=False)

        logger.debug("Raw model output (%d chars)", len(raw))

        # Try to parse with sanitization
        sanitized = _sanitize_model_output(raw)
        try:
            payload = json.loads(sanitized)
        except json.JSONDecodeError as exc:
            # Try more aggressive sanitization
            try:
                # Remove control characters and fix escapes
                sanitized2 = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', sanitized)
                # Try to fix common issues
                sanitized2 = sanitized2.replace('\\/', '/')
                payload = json.loads(sanitized2)
            except json.JSONDecodeError:
                raise ContentValidationError(f"Model returned invalid JSON: {exc}") from exc
        return validate_payload(payload)

    def _generate_once(self, prompt: str) -> GeneratedPost:
        return retry(
            lambda: self._call_model(prompt),
            attempts=5,
            exceptions=(Exception,),
            label="llm-generate",
        )

    def generate(
        self,
        topic: str,
        *,
        used_titles: list[str],
        used_topics: list[str],
    ) -> GeneratedPost:
        prompt = build_user_prompt(
            topic,
            niche=self.cfg.blog_niche,
            language=self.cfg.blog_language,
            tone=self.cfg.tone,
            min_words=self.cfg.min_words,
            max_words=self.cfg.max_words,
            used_titles=used_titles,
            used_topics=used_topics,
        )
        logger.info("Generating post with model %s (topic: %s)", self.model, topic)

        post = self._generate_once(prompt)
        words = word_count(post.html_body)
        attempt = 1

        # Check for markdown and regenerate if found
        if '**' in post.html_body or '##' in post.html_body or re.search(r'^\s*#{1,6}\s', post.html_body, re.M):
            logger.warning("Markdown artifacts found, regenerating")
            nudge_md = "\n\nCRITICAL: Output PURE HTML ONLY. NO markdown. Use <ul><li><strong>text</strong></li></ul> for lists. NO ** or ## characters anywhere."
            post = self._generate_once(prompt + nudge_md)
            words = word_count(post.html_body)

        while words < self.cfg.min_words and attempt < LENGTH_ATTEMPTS:
            attempt += 1
            logger.warning(
                "Post is only %d words (target %d), regenerating (attempt %d/%d)",
                words,
                self.cfg.min_words,
                attempt,
                LENGTH_ATTEMPTS,
            )
            nudge = (
                "\n\nIMPORTANT: your previous attempt was only {words} words. "
                "This time write a much fuller post: at least {min_words} words "
                "and no more than {max_words}. Add more sections, more detail in "
                "each bullet point, and longer explanations. Do not repeat the "
                "structure you used before, expand it. Output PURE HTML only, no markdown."
            ).format(words=words, min_words=self.cfg.min_words, max_words=self.cfg.max_words)
            post = self._generate_once(prompt + nudge)
            words = word_count(post.html_body)
            if '**' in post.html_body or '##' in post.html_body:
                attempt = LENGTH_ATTEMPTS  # force regenerate again
                break

        if words < self.cfg.min_words:
            logger.warning(
                "Post is still short at %d words, continuing anyway (hard floor is %d)",
                words,
                MIN_HARD_FLOOR,
            )
        else:
            logger.info("Post length ok: %d words", words)

        post.topic = topic
        return post