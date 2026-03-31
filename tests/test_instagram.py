"""Tests for Instagram metadata extraction module."""
import pytest
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
