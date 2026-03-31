"""Tests for Instagram metadata extraction module."""
import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from instagram import extract_instagram_metadata, _parse_yt_dlp_info


def test_parse_yt_dlp_info_with_caption():
    info = {
        "description": "Hello world! #test",
        "uploader": "testuser",
        "extractor_key": "Instagram",
        "_type": "url",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] == "Hello world! #test"
    assert result["author"] == "testuser"
    assert result["content_type"] == "Post"


def test_parse_yt_dlp_info_reel():
    info = {
        "description": None,
        "uploader": "reelcreator",
        "extractor_key": "Instagram",
        "product_type": "clips",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] is None
    assert result["author"] == "reelcreator"
    assert result["content_type"] == "Reel"


def test_parse_yt_dlp_info_story():
    info = {
        "description": "Story text here",
        "uploader": "storyuser",
        "product_type": "story",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] == "Story text here"
    assert result["content_type"] == "Story"


def test_extract_instagram_metadata_success():
    """Test successful metadata extraction with caption."""
    sample_info = {
        "description": "This is a test caption",
        "uploader": "testuser",
        "product_type": "clips",
    }
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(sample_info),
            stderr=""
        )
        result = extract_instagram_metadata("https://instagram.com/p/test")
        assert result["caption"] == "This is a test caption"
        assert result["author"] == "testuser"
        assert result["content_type"] == "Reel"
        mock_run.assert_called_once()


def test_extract_instagram_metadata_no_caption():
    """Test metadata extraction when no caption exists."""
    sample_info = {
        "description": None,
        "uploader": "reelcreator",
        "product_type": "clips",
    }
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(sample_info),
            stderr=""
        )
        result = extract_instagram_metadata("https://instagram.com/reel/test")
        assert result["caption"] is None
        assert result["author"] == "reelcreator"
        assert result["content_type"] == "Reel"


def test_extract_instagram_metadata_yt_dlp_failure():
    """Test that RuntimeError is raised when yt-dlp fails."""
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Video unavailable"
        )
        try:
            extract_instagram_metadata("https://instagram.com/p/test")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "yt-dlp failed" in str(e)


def test_extract_instagram_metadata_timeout():
    """Test that RuntimeError is raised on timeout."""
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30)
        try:
            extract_instagram_metadata("https://instagram.com/p/test")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "timed out" in str(e)


def test_extract_instagram_metadata_invalid_json():
    """Test that RuntimeError is raised on invalid JSON."""
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
            stderr=""
        )
        try:
            extract_instagram_metadata("https://instagram.com/p/test")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Failed to parse" in str(e)


def test_extract_instagram_metadata_empty_output():
    """Test that RuntimeError is raised on empty output."""
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )
        try:
            extract_instagram_metadata("https://instagram.com/p/test")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "No metadata" in str(e)


def test_extract_instagram_metadata_strips_at_prefix():
    """Test that @ prefix is stripped from author name."""
    sample_info = {
        "description": "Test caption",
        "uploader": "@testuser",
    }
    with patch("instagram.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(sample_info),
            stderr=""
        )
        result = extract_instagram_metadata("https://instagram.com/p/test")
        assert result["author"] == "testuser"
