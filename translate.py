"""Translation and TTS pipeline — independent functions for text translation, audio transcription, and speech synthesis."""
import logging
import os
import sys
from pathlib import Path
from google import genai
from download_audio import download_audio

logger = logging.getLogger("bot.translate")

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")


def convert_to_malayalam(input_path: str) -> str:
    """Translate text or transcribe+translate audio to Malayalam.

    Accepts a file path or URL. URLs are downloaded first via yt-dlp.

    Returns the translated Malayalam text.
    Raises RuntimeError on failure.
    """
    if input_path.startswith("http://") or input_path.startswith("https://"):
        logger.info("URL detected, downloading...")
        input_path = download_audio(input_path)
        logger.info("Downloaded to: %s", input_path)

    path = Path(input_path)
    if path.exists():
        logger.info("File exists: %s, size=%d", path, path.stat().st_size)
    else:
        logger.info("Not a file, treating as text: %s", input_path[:100])

    client = genai.Client()
    model = "models/gemini-3.1-flash-lite-preview"
    config = {"temperature": 0.1}

    if path.suffix.lower() == ".mp3":
        logger.info("Uploading audio to Gemini...")
        myfile = client.files.upload(file=str(path))
        logger.info("Uploaded: %s", myfile.name)
        try:
            logger.info("Calling Gemini for transcription...")
            response = client.models.generate_content(
                model=model,
                contents=[SYSTEM_PROMPT, myfile],
                config=config,
            )
        finally:
            try:
                if myfile.name:
                    client.files.delete(name=myfile.name)
            except Exception:
                pass
    elif path.exists():
        source_text = path.read_text(encoding="utf-8")
        response = client.models.generate_content(
            model=model,
            contents=[SYSTEM_PROMPT, f"Input Text to Convert:\n\n{source_text}"],
            config=config,
        )
    else:
        response = client.models.generate_content(
            model=model,
            contents=[SYSTEM_PROMPT, f"Input Text to Convert:\n\n{input_path}"],
            config=config,
        )

    if not response.text:
        logger.error("Gemini returned empty response")
        raise RuntimeError("Gemini returned empty response")

    logger.info("Translated text length: %d", len(response.text))
    return response.text.strip()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 translate.py <input> [output_file]")
        print("  input: text file, audio file, or URL (YouTube, Instagram, etc.)")
        sys.exit(1)

    input_arg = sys.argv[1]
    result = convert_to_malayalam(input_arg)

    if input_arg.startswith("http://") or input_arg.startswith("https://"):
        default_name = "audiobook.txt"
    else:
        default_name = Path(input_arg).stem + "_audiobook.txt"

    output_file = sys.argv[2] if len(sys.argv) > 2 else default_name
    Path(output_file).write_text(result, encoding="utf-8")
    print(f"✅ Success! Saved to {output_file}")
