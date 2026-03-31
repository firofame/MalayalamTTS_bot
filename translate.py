"""Translation and TTS pipeline — independent functions for text translation, audio transcription, and speech synthesis."""
import sys
import os
import subprocess
from pathlib import Path
from google import genai

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus', '.webm'}

# Separate prompts for text vs audio
TEXT_TRANSLATION_PROMPT = (
    "Detect the source language of the following text and translate it to Malayalam.\n"
    "Preserve emojis, line breaks, and formatting.\n"
    "If the text contains mixed languages, translate everything to Malayalam.\n"
    "Return only the translated text, no explanations or introductions.\n"
)

AUDIO_TRANSCRIPTION_PROMPT = (
    "Transcribe the audio and translate the result to Malayalam.\n"
    "Return only the translated text, no explanations or introductions.\n"
)


def _get_genai_client() -> genai.Client:
    """Create and return a Gemini API client."""
    return genai.Client()


def translate_text(text: str) -> str:
    """Translate text to Malayalam using Gemini.

    Args:
        text: Source text in any language (commonly Arabic).

    Returns:
        Translated Malayalam text, or empty string on failure.
    """
    client = _get_genai_client()
    response = client.models.generate_content(
        model="models/gemini-3.1-flash-lite-preview",
        contents=[TEXT_TRANSLATION_PROMPT, f"Input Text to Convert:\n\n{text}"],
        config={"temperature": 0.1},
    )
    return response.text.strip() if response.text else ""


def transcribe_audio(audio_path: str) -> str:
    """Transcribe audio file and translate to Malayalam using Gemini.

    Args:
        audio_path: Path to audio file.

    Returns:
        Translated Malayalam text, or empty string on failure.
    """
    client = _get_genai_client()
    myfile = client.files.upload(file=str(audio_path))
    try:
        response = client.models.generate_content(
            model="models/gemini-3.1-flash-lite-preview",
            contents=[AUDIO_TRANSCRIPTION_PROMPT, myfile],
            config={"temperature": 0.1},
        )
        return response.text.strip() if response.text else ""
    finally:
        try:
            client.files.delete(name=myfile.name)
        except Exception:
            pass  # Best-effort cleanup; don't fail transcription on cleanup error


def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path.

    Args:
        url: YouTube, Instagram, or other supported URL.

    Returns:
        Path to downloaded mp3 file.
    Raises:
        RuntimeError if yt-dlp fails or no file is found.
    """
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_template, url],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    combined = result.stdout + result.stderr
    for line in combined.splitlines():
        if "[ExtractAudio] Destination:" in line:
            path = line.split("Destination:")[-1].strip()
            if path.endswith(".mp3") and Path(path).exists():
                return path

    mp3s = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp3s:
        return str(mp3s[0])

    raise RuntimeError("Could not find downloaded audio file")


def convert_to_audiobook_script(input_file: str, output_file: str) -> None:
    """Legacy function — transcribes/translates and saves to file.

    Kept for backward compatibility with CLI usage.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: {input_file} not found")
        return

    is_audio = input_path.suffix.lower() in AUDIO_EXTENSIONS

    if is_audio:
        result = transcribe_audio(input_file)
    else:
        source_text = input_path.read_text(encoding="utf-8")
        result = translate_text(source_text)

    if not result:
        print("Error: Gemini returned empty response.")
        return

    Path(output_file).write_text(result, encoding="utf-8")
    print(f"✅ Success! Saved audiobook script to {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 translate.py <input> [output_file]")
        print("  input: text file, audio file, or URL (YouTube, Instagram, etc.)")
        sys.exit(1)

    input_arg = sys.argv[1]

    if input_arg.startswith("http://") or input_arg.startswith("https://"):
        input_file = download_audio(input_arg)
        default_name = Path(input_file).stem + "_audiobook.txt"
    else:
        input_file = input_arg
        default_name = Path(input_file).stem + "_audiobook.txt"

    output_file = sys.argv[2] if len(sys.argv) > 2 else str(Path(input_file).parent / default_name)
    convert_to_audiobook_script(input_file, output_file)
