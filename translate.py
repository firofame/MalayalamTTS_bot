"""Translate Telegram message text to Malayalam."""
import logging
import os
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger("bot.translate")

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")

# Validate API key at import time
if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
    logger.warning("No GOOGLE_API_KEY or GEMINI_API_KEY found — Gemini calls will fail")

client = genai.Client(http_options={"timeout": 120_000})
MODEL = "models/gemini-3.5-flash-lite"
CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM),
)


async def convert_to_malayalam(text: str) -> str:
    """Translate plain text to Malayalam.

    Returns the translated Malayalam text.
    Raises RuntimeError on failure.
    """
    logger.info("Translating text: text_len=%d", len(text))
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=[SYSTEM_PROMPT, f"Input Text to Convert:\n\n{text}"],
        config=CONFIG,
    )

    if not response.text:
        logger.error("Gemini returned empty response")
        raise RuntimeError("Gemini returned empty response")

    logger.info("Translated text length: %d", len(response.text))
    return response.text.strip()
