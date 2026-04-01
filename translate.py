"""Translation and TTS pipeline — independent functions for text translation, audio transcription, and speech synthesis."""
import sys
import subprocess
from pathlib import Path
from google import genai

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus', '.webm'}

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")


def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path."""
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_template, url],
        capture_output=True,
        text=True,
        timeout=120,
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


def convert_to_malayalam(input_path: str) -> str:
    """Translate text or transcribe+translate audio to Malayalam.

    Accepts a file path or URL. URLs are downloaded first via yt-dlp.

    Returns the translated Malayalam text.
    Raises RuntimeError on failure.
    """
    if input_path.startswith("http://") or input_path.startswith("https://"):
        input_path = download_audio(input_path)

    path = Path(input_path)
    if not path.exists():
        raise RuntimeError(f"File not found: {input_path}")

    client = genai.Client()
    model = "models/gemini-3.1-flash-lite-preview"
    config = {"temperature": 0.1}

    if path.suffix.lower() in AUDIO_EXTENSIONS:
        myfile = client.files.upload(file=str(path))
        try:
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
    else:
        source_text = path.read_text(encoding="utf-8")
        response = client.models.generate_content(
            model=model,
            contents=[SYSTEM_PROMPT, f"Input Text to Convert:\n\n{source_text}"],
            config=config,
        )

    if not response.text:
        raise RuntimeError("Gemini returned empty response")

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
