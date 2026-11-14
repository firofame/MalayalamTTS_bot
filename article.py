"""Fetch readable article text from a URL using the Jina Reader service."""
import logging
import os
import re

import httpx

from tts import SENTENCE_MARKS, sanitize_text

logger = logging.getLogger("bot.article")

READER_URL = "https://r.jina.ai/"
MAX_ARTICLE_LENGTH = 40_000
# A whole message that is nothing but a URL is treated as an article request.
URL_PATTERN = re.compile(r"https?://\S+")
CONTENT_MARKER = "Markdown Content:\n"
TITLE_PREFIX = "Title: "


class ArticleError(RuntimeError):
    pass


def is_url(text: str) -> bool:
    return URL_PATTERN.fullmatch(text.strip()) is not None


def _parse_reader_response(body: str) -> tuple[str, str]:
    """Split Jina Reader output into its title and markdown content."""
    index = body.find(CONTENT_MARKER)
    if index == -1:
        return "", body
    title = ""
    for line in body[:index].splitlines():
        if line.startswith(TITLE_PREFIX):
            title = line[len(TITLE_PREFIX):].strip()
            break
    return title, body[index + len(CONTENT_MARKER):]


def _strip_markdown(text: str) -> str:
    """Convert markdown to plain prose suitable for speech synthesis."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                        # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                     # links keep their text
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"^[ \t]*([-*_][ \t]*){3,}$", " ", text, flags=re.MULTILINE)  # horizontal rules
    text = re.sub(r"`{1,3}", " ", text)                                      # code markers
    text = re.sub(r"\*+|_+", " ", text)                                      # emphasis markers
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.MULTILINE)           # blockquotes
    text = re.sub(r"^[ \t]*[-*+][ \t]+", "", text, flags=re.MULTILINE)       # list bullets
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str) -> str:
    """Cut to MAX_ARTICLE_LENGTH, ending on a sentence boundary when one is nearby."""
    window = text[:MAX_ARTICLE_LENGTH]
    cut = max(window.rfind(mark) for mark in SENTENCE_MARKS)
    if cut > MAX_ARTICLE_LENGTH // 2:
        window = window[:cut + 1]
    return window.rstrip()


async def extract(http_client: httpx.AsyncClient, url: str) -> tuple[str, str, bool]:
    """Return the article's title, speech-ready text, and whether it was truncated."""
    headers = {}
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = await http_client.get(f"{READER_URL}{url}", headers=headers, timeout=60.0)
    except httpx.HTTPError as error:
        logger.error("Jina Reader request failed (%s)", type(error).__name__)
        raise ArticleError("Could not reach the article service. Please try again later.") from None
    if response.status_code == 429:
        raise ArticleError("Article service rate limit reached. Please try again later.")
    if response.status_code in (401, 403, 451):
        raise ArticleError("Could not read this page. It may be paywalled, private, or blocked.")
    if response.status_code != 200:
        raise ArticleError("Could not read this page. It may be unavailable or unsupported.")
    body = response.text
    if not body.strip():
        raise ArticleError("The article service returned an empty page.")
    title, markdown = _parse_reader_response(body)
    content = sanitize_text(_strip_markdown(markdown))
    if not content:
        raise ArticleError("No readable article text found on this page.")
    truncated = len(content) > MAX_ARTICLE_LENGTH
    if truncated:
        content = _truncate(content)
    return title, content, truncated
