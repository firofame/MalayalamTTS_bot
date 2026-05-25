# Malayalam TTS Bot — Improvement Plan

## ✅ Completed Fixes

### 🔴 Critical (Phase 1)

| # | Issue | Fix Applied |
|---|---|---|
| 1 | ~~Invalid Gemini model name~~ | Changed to `gemini-2.5-flash` in `translate.py` |
| 2 | ~~No `BOT_TOKEN` validation on startup~~ | `RuntimeError` raised at module load if missing |
| 3 | ~~No pinned dependency versions~~ | All packages pinned with compatible-release ranges |

### 🟡 Bugs / Fragile Code (Phase 2)

| # | Issue | Fix Applied |
|---|---|---|
| 4 | ~~`asyncio.run()` inside a thread~~ | Replaced with `asyncio.new_event_loop()` + `run_until_complete()` + `close()` |
| 5 | ~~`_rate_limits` dict accessed without locks~~ | Added `threading.Lock` wrapping all access |
| 6 | ~~yt-dlp stdout parsing fragile~~ | Uses `--print after_move:filepath` flag |
| 7 | ~~Cookie file world-readable, never cleaned up~~ | `os.chmod(0o600)` + `_cleanup_cookies()` in `finally` block |
| 8 | ~~Chat action thread lifecycle bugs~~ | New `threading.Event` per phase, `join(timeout=5)` |
| 9 | ~~Logging f-string with `%s`~~ | **Removed — false positive.** The logging call was already correct |
| 10 | ~~`fastapi[all]` installs unused extras~~ | Replaced with `fastapi`, `uvicorn`, `python-multipart` |

### Additional fixes (found during validation)

| # | Issue | Fix Applied |
|---|---|---|
| A | ~~No `GOOGLE_API_KEY` validation~~ | Warning logged at import time if key missing |
| B | ~~Downloaded source audio never cleaned up~~ | `_cleanup_downloads()` removes files older than 1 hour |
| C | ~~No request timeouts on HTTP calls~~ | `timeout=10` on Telegram API, `timeout=60` on `sendVoice`, `timeout=120s` on Gemini |
| D | ~~HTML injection in error messages~~ | `html.escape()` on error strings before HTML send |
| E | ~~Deprecated `@app.on_event("startup")`~~ | Replaced with `lifespan` async context manager |
| F | ~~No timeout on Gemini API calls~~ | `genai.Client(http_options={"timeout": 120_000})` |

---

## 🟢 Remaining — Architecture & Code Quality (Phase 3)

| # | Issue | Fix |
|---|---|---|
| 11 | Mix of sync/async — `requests` + `asyncio` + `threading` | Migrate to fully async with `httpx` |
| 12 | No `__init__.py` — not a proper Python package | Add `__init__.py` |
| 13 | ~~No URL validation~~ | ✅ Done — uses `urllib.parse` |
| 14 | ~~No text length limits~~ | ✅ Done — 4000 char max |
| 15 | No file type validation for downloads | Add post-download validation |
| 16 | ~~`downloads/` directory accumulates files forever~~ | ✅ Done — TTL-based cleanup |
| 17 | Minimal type hints | Add full type annotations throughout |
| 18 | ~~No input sanitization~~ | ✅ Done — strips control characters |

## 🔵 Remaining — Testing & Deployment (Phase 4)

| # | Suggestion |
|---|---|
| 19 | Add unit tests for `translate.py` and `download_audio.py` |
| 20 | Add integration test for webhook handler |
| 21 | Add `Dockerfile` for consistent deployment |
| 22 | Add `ruff` / `mypy` config for linting |
| 23 | Add GitHub Actions CI |

## Implementation Order

1. **Phase 1 — Critical fixes**: ✅ Complete
2. **Phase 2 — Bug fixes**: ✅ Complete
3. **Phase 3 — Code quality**: Items 11, 12, 15, 17 remaining
4. **Phase 4 — Testing & infra**: Items 19-23 remaining
