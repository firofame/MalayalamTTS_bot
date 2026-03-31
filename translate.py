import sys
import os
import subprocess
from pathlib import Path
from google import genai

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus', '.webm'}

def load_prompt(filename: str) -> str:
    """Load a prompt file relative to this script."""
    path = Path(__file__).parent / filename
    return path.read_text(encoding="utf-8").strip()

SYSTEM_PROMPT = load_prompt("prompt.txt")

def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path."""
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    print(f"Downloading audio from {url}...")
    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_template, url],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Error: yt-dlp failed:\n{result.stderr}")
        sys.exit(1)

    # Find the downloaded file from stdout
    for line in result.stdout.splitlines():
        if "[ExtractAudio] Destination:" in line:
            path = line.split("Destination:")[-1].strip()
            return path
        if "[download] Destination:" in line:
            path = line.split("Destination:")[-1].strip()
            return path

    # Fallback: find newest mp3 in downloads/
    mp3s = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp3s:
        return str(mp3s[0])

    print("Error: Could not find downloaded audio file")
    sys.exit(1)

def convert_to_audiobook_script(input_file: str, output_file: str):
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: {input_file} not found")
        return

    client = genai.Client()
    is_audio = input_path.suffix.lower() in AUDIO_EXTENSIONS

    if is_audio:
        print(f"Uploading audio file {input_file}...")
        myfile = client.files.upload(file=str(input_path))
        contents = [SYSTEM_PROMPT, myfile]
        print(f"Transcribing and converting to TTS-optimized script...")
    else:
        print(f"Reading text from {input_file}...")
        source_text = input_path.read_text(encoding="utf-8")
        contents = [SYSTEM_PROMPT, f"Input Text to Convert:\n\n{source_text}"]
        print(f"Converting to TTS-optimized script...")

    response = client.models.generate_content(
        model="models/gemini-3.1-flash-lite-preview",
        contents=contents,
        config={"temperature": 0.1},
    )

    if not response.text:
        print("Error: Gemini returned empty response.")
        return

    result_text = response.text.strip()
    
    Path(output_file).write_text(result_text, encoding="utf-8")
    print(f"✅ Success! Saved audiobook script to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 translate.py <input> [output_file]")
        print("  input: text file, audio file, or URL (YouTube, Instagram, etc.)")
        sys.exit(1)

    input_arg = sys.argv[1]

    # Handle URL input — download audio via yt-dlp
    if input_arg.startswith("http://") or input_arg.startswith("https://"):
        input_file = download_audio(input_arg)
        default_name = Path(input_file).stem + "_audiobook.txt"
    else:
        input_file = input_arg
        default_name = Path(input_file).stem + "_audiobook.txt"

    output_file = sys.argv[2] if len(sys.argv) > 2 else str(Path(input_file).parent / default_name)

    convert_to_audiobook_script(input_file, output_file)
