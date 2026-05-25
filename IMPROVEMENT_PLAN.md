# Malayalam TTS Bot — Improvement Plan

## 🔴 Critical (will break soon / silently fail)

| # | Issue | File:Line | Fix |
|---|---|---|---|
| 1 | Invalid Gemini model name `gemini-3.1-flash-lite-preview` — no known published model by that name | `translate.py:13` | Update to `gemini-2.0-flash-lite` or `gemini-2.0-flash` |
| 2 | No `BOT_TOKEN` validation on startup — app starts but all API calls silently fail | `main.py:30` | Add startup check that raises if missing |
| 3 | No pinned dependency versions — deploy can break anytime | `requirements.txt` | Pin all packages to known-good versions |

## 🟡 Bugs / Fragile Code

| # | Issue | File:Line | Fix |
|---|---|---|---|
| 4 | `asyncio.run()` inside a thread — creates new event loop each time, fragile | `main.py:194` | Convert to proper async OR use `asyncio.new_event_loop()` |
| 5 | `_rate_limits` dict accessed without locks — thread-unsafe race condition | `main.py:35-123` | Add `threading.Lock` |
| 6 | yt-dlp stdout parsing — `[ExtractAudio] Destination:` string matching is locale/version-dependent | `download_audio.py:53-57` | Use `--print filename` flag or JSON output |
| 7 | Cookie file copied to world-readable temp dir, never cleaned up | `download_audio.py:18-23` | Clean up temp cookies after download completes |
| 8 | Chat action thread lifecycle bugs — double-join, race between set/clear, no exception isolation | `main.py:170-185` | Refactor with cleaner thread lifecycle |
| 9 | Logging f-string with `%s` — `args_len=%d` inside an f-string mixing `%s` formatting | `main.py:150` | Use either f-string or `%s`, not both |
| 10 | `fastapi[all]` installs many unused extras | `requirements.txt` | Pin to `fastapi`, `uvicorn`, `python-multipart` individually |

## 🟢 Architecture & Code Quality

| # | Issue | Fix |
|---|---|---|
| 11 | Mix of sync/async — `requests` + `asyncio.run()` + `threading` | Migrate to fully async with `httpx` |
| 12 | No `__init__.py` — not a proper Python package | Add `__init__.py` |
| 13 | No URL validation — just `startswith("http")` | Use `urllib.parse` for proper URL validation |
| 14 | No text length limits — could receive megabytes | Add max length check (e.g., 4000 chars) |
| 15 | No file type validation for downloads — relies entirely on yt-dlp | Add post-download validation |
| 16 | `downloads/` directory accumulates files forever | Add TTL-based cleanup (delete files older than 1 hour) |
| 17 | Minimal type hints | Add full type annotations throughout |
| 18 | No input sanitization | Strip control characters, limit input size |

## 🔵 Testing & Deployment

| # | Suggestion |
|---|---|
| 19 | Add unit tests for `translate.py` and `download_audio.py` |
| 20 | Add integration test for webhook handler |
| 21 | Add `Dockerfile` for consistent deployment |
| 22 | Add `ruff` / `mypy` config for linting |
| 23 | Add GitHub Actions CI |

## Implementation Order

1. **Phase 1 — Critical fixes**: Items 1, 2, 3 (prevent app from breaking)
2. **Phase 2 — Bug fixes**: Items 4, 5, 6, 7, 8, 9, 10 (stability / correctness)
3. **Phase 3 — Code quality**: Items 11-18 (maintainability)
4. **Phase 4 — Testing & infra**: Items 19-23 (confidence)
