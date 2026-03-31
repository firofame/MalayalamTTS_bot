"""Instagram content extraction — fetches caption and metadata without downloading audio."""
import subprocess
import json


def _parse_yt_dlp_info(info: dict) -> dict:
    """Parse yt-dlp JSON output into a normalized metadata dict."""
    caption = info.get("description") or None
    if caption:
        caption = caption.strip()
        if not caption:
            caption = None

    uploader = info.get("uploader") or info.get("channel") or ""
    # Remove @ prefix if present for display
    author = uploader.lstrip("@")

    # Determine content type
    product = info.get("product_type", "").lower()
    if product == "clips":
        content_type = "Reel"
    elif product == "story":
        content_type = "Story"
    else:
        content_type = "Post"

    return {
        "caption": caption,
        "author": author,
        "content_type": content_type,
    }


def extract_instagram_metadata(url: str) -> dict:
    """Extract caption, author, and content type from an Instagram URL.

    Uses yt-dlp in metadata-only mode (no download).
    Returns: {"caption": str|None, "author": str, "content_type": str}
    Raises: RuntimeError if yt-dlp fails or URL is not extractable.
    """
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--no-download",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out while fetching metadata")

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    # yt-dlp outputs one JSON object per line (may have multiple for playlists)
    # Take the last non-empty line as the primary item
    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("No metadata returned by yt-dlp")

    try:
        info = json.loads(lines[-1])
    except json.JSONDecodeError:
        raise RuntimeError("Failed to parse yt-dlp metadata JSON")

    return _parse_yt_dlp_info(info)
