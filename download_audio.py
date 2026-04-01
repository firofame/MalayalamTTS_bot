"""Download audio from URLs using yt-dlp."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_COOKIES_SOURCE = "/etc/secrets/cookies.txt"


def _get_cookies_arg() -> list[str]:
    """Return yt-dlp cookies args, copying to a writable path if needed."""
    if not Path(_COOKIES_SOURCE).exists():
        return []
    writable = os.path.join(tempfile.gettempdir(), "yt-dlp-cookies.txt")
    try:
        shutil.copy2(_COOKIES_SOURCE, writable)
    except Exception:
        return []
    return ["--cookies", writable]


def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path."""
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    print(f"[download_audio] Starting download: {url}")
    cookies_args = _get_cookies_arg()
    print(f"[download_audio] Cookies: {bool(cookies_args)}")

    cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_template] + cookies_args + [url]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

    print(f"[download_audio] Exit code: {result.returncode}")
    print(f"[download_audio] stdout: {result.stdout[:500]}")
    print(f"[download_audio] stderr: {result.stderr[:500]}")

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
