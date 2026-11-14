import os
import unittest
from unittest.mock import Mock, patch

import httpx

import article
from article import ArticleError, extract, is_url

SAMPLE_BODY = (
    "Title: My Post\n"
    "URL Source: https://example.test/post\n\n"
    "Markdown Content:\n"
    "# My Post\n\n"
    "Hello [world](https://example.test/link)!\n\n"
    "![image](https://example.test/pic.png)\n\n"
    "- **Bold** item\n"
    "- _Emphasized_ item\n"
    "\n\n\n"
    "> A quote.\n"
    "---\n"
    "```\ncode()\n```\n"
)


class ExtractTests(unittest.IsolatedAsyncioTestCase):
    def client(self, response=None, side_effect=None):
        handle = Mock(return_value=response, side_effect=side_effect)
        return handle, httpx.AsyncClient(transport=httpx.MockTransport(handle))

    async def test_parses_title_and_strips_markdown(self):
        handle, client = self.client(httpx.Response(200, text=SAMPLE_BODY))
        self.addAsyncCleanup(client.aclose)
        title, content, truncated = await extract(client, "https://example.test/post")
        self.assertEqual((title, truncated), ("My Post", False))
        self.assertEqual(content, "My Post\n\nHello world!\n\nBold item\nEmphasized item\n\nA quote.\n\ncode()")
        request = handle.call_args.args[0]
        self.assertEqual(str(request.url), "https://r.jina.ai/https://example.test/post")
        self.assertNotIn("Authorization", request.headers)

    async def test_pages_without_reader_markers_use_whole_body(self):
        handle, client = self.client(httpx.Response(200, text="Just plain text with no markers."))
        self.addAsyncCleanup(client.aclose)
        title, content, truncated = await extract(client, "https://example.test/post")
        self.assertEqual((title, content, truncated), ("", "Just plain text with no markers.", False))

    async def test_api_key_is_sent_only_when_configured(self):
        handle, client = self.client(httpx.Response(200, text="Title: T\n\nMarkdown Content:\nHi"))
        self.addAsyncCleanup(client.aclose)
        with patch.dict(os.environ, {"JINA_API_KEY": "test-key"}):
            await extract(client, "https://example.test/post")
        self.assertEqual(handle.call_args.args[0].headers["Authorization"], "Bearer test-key")
        handle, client = self.client(httpx.Response(200, text="Title: T\n\nMarkdown Content:\nHi"))
        self.addAsyncCleanup(client.aclose)
        with patch.dict(os.environ, {"JINA_API_KEY": ""}):
            await extract(client, "https://example.test/post")
        self.assertNotIn("Authorization", handle.call_args.args[0].headers)

    async def test_oversized_articles_are_truncated_at_sentence_boundary(self):
        body = "Title: Long\n\nMarkdown Content:\n" + "A sentence with some words. " * 2000
        handle, client = self.client(httpx.Response(200, text=body))
        self.addAsyncCleanup(client.aclose)
        title, content, truncated = await extract(client, "https://example.test/post")
        self.assertEqual(title, "Long")
        self.assertTrue(truncated)
        self.assertLessEqual(len(content), article.MAX_ARTICLE_LENGTH)
        self.assertTrue(content.endswith("."))

    async def test_upstream_errors_are_explicit_without_retries(self):
        for status, expected in (
            (429, "rate limit"), (401, "Could not read"), (403, "Could not read"),
            (451, "Could not read"), (500, "Could not read"),
        ):
            with self.subTest(status=status):
                handle, client = self.client(httpx.Response(status))
                self.addAsyncCleanup(client.aclose)
                with self.assertRaisesRegex(ArticleError, expected):
                    await extract(client, "https://example.test/post")
                handle.assert_called_once()

    async def test_network_failures_are_reported(self):
        handle, client = self.client(side_effect=httpx.ConnectError("Failed"))
        self.addAsyncCleanup(client.aclose)
        with self.assertLogs("bot.article", level="ERROR"):
            with self.assertRaisesRegex(ArticleError, "Could not reach"):
                await extract(client, "https://example.test/post")

    async def test_empty_and_contentless_pages_are_rejected(self):
        for response, expected in (
            (httpx.Response(200, text="   "), "empty page"),
            (httpx.Response(200, text="Title: X\n\nMarkdown Content:\n \n"), "No readable"),
        ):
            with self.subTest(expected=expected):
                handle, client = self.client(response)
                self.addAsyncCleanup(client.aclose)
                with self.assertRaisesRegex(ArticleError, expected):
                    await extract(client, "https://example.test/post")


class IsUrlTests(unittest.TestCase):
    def test_whole_message_urls_match_and_other_text_does_not(self):
        for text, expected in (
            ("https://example.test/post", True),
            ("  https://example.test/post  ", True),
            ("http://example.test", True),
            ("See https://example.test/post for details", False),
            ("/url https://example.test/post", False),
            ("ftp://example.test/file", False),
            ("not a url", False),
        ):
            with self.subTest(text=text):
                self.assertEqual(is_url(text), expected)


if __name__ == "__main__":
    unittest.main()
