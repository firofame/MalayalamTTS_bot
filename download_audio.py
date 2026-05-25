"""Download audio from URLs using yt-dlp."""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("bot.download")

_COOKIES_SOURCE = "/etc/secrets/cookies.txt"


def _get_cookies_arg() -> list[str]:
    """Return yt-dlp cookies args, copying to a writable temp path.

    The temp cookie file is cleaned up by the caller via cleanup_cookies().
    """
    if not Path(_COOKIES_SOURCE).exists():
        return []
    writable = os.path.join(tempfile.gettempdir(), "yt-dlp-cookies.txt")
    try:
        shutil.copy2(_COOKIES_SOURCE, writable)
        os.chmod(writable, 0o600)  # Restrict permissions
    except Exception:
        return []
    return ["--cookies", writable]


def _cleanup_cookies() -> None:
    """Remove the temporary cookie file if it exists."""
    writable = os.path.join(tempfile.gettempdir(), "yt-dlp-cookies.txt")
    try:
        if os.path.exists(writable):
            os.remove(writable)
    except OSError:
        pass


def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path.

    Caller is responsible for deleting the returned file when done.
    """
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    logger.info("Starting download: %s", url)
    cookies_args = _get_cookies_arg()
    logger.debug("Cookies available: %s", bool(cookies_args))

    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3",
        "-o", output_template,
        "--print", "after_move:filepath",  # Print final filename to stdout
    ] + cookies_args + [url]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        _cleanup_cookies()

    logger.debug("Exit code: %s", result.returncode)
    logger.debug("stdout: %s", result.stdout[:500])
    logger.debug("stderr: %s", result.stderr[:500])

    if result.returncode != 0:
        logger.error("yt-dlp failed: %s", result.stderr.strip())
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    # --print after_move:filepath outputs the final path as the last line of stdout
    for line in reversed(result.stdout.strip().splitlines()):
        path = line.strip()
        if path and Path(path).exists():
            logger.info("Downloaded: %s", path)
            return path

    # Fallback: find most recent mp3
    mp3s = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp3s:
        return str(mp3s[0])

    raise RuntimeError("Could not find downloaded audio file")
