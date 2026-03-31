# Instagram Translation UX Enhancement — Design Spec

## Overview

Enhance the MalayalamTTS_bot to provide a richer, more transparent translation experience when users share Instagram links. Currently users receive only a voice message with no visibility into the translation. The new flow sends translated text first (instant feedback), then follows with audio.

## User Flow

1. User sends Instagram link
2. Bot sends progress message: "📥 Downloading..."
3. Extract caption/metadata from Instagram (yt-dlp metadata extraction, no audio download needed if caption exists)
4. If caption exists: use it directly. If no caption: download audio + transcribe via Gemini
5. Edit progress to: "🌐 Translating from [Language]..."
6. Translate text to Malayalam via Gemini
7. **Send translated text message** (with source language, content type, author)
8. Edit progress to: "🎙️ Generating audio..."
9. Generate speech via Edge TTS
10. Send voice message

## Architecture

### New File: `instagram.py`
- `extract_instagram_metadata(url)` — uses yt-dlp to fetch caption, author, content type, audio URL without downloading audio
- Returns: `{caption: str|None, audio_url: str|None, content_type: str, author: str}`
- Caption-first strategy: if caption exists, skip audio download entirely (faster, cheaper)
- Fallback: if no caption or caption is empty, download audio and transcribe

### Updated: `translate.py`
Split into independent functions:

- `translate_text(text, source_lang=None)` — Gemini call to detect source language and translate to Malayalam. Preserves emojis, line breaks, formatting. Handles Arabic RTL source text.
- `transcribe_audio(audio_path)` — Gemini audio transcription + translation to Malayalam
- `text_to_speech(malayalam_text, voice=VOICE)` — Edge TTS wrapper
- Each function is independent and testable in isolation

### Updated: `main.py`
- Orchestrate the flow with progress messages
- Use `editMessageText` for progress updates (cleaner chat, no message spam)
- Send translation text message before audio generation
- Handle partial failures gracefully (text succeeds but TTS fails → still deliver text)

## Translation Pipeline

### Gemini Prompt (Text Translation)
```
Detect the source language and translate the following text to Malayalam.
Preserve emojis, line breaks, and formatting.
If the text contains mixed languages, translate everything to Malayalam.
Return only the translated text, no explanations.
```

### Gemini Prompt (Audio Transcription)
```
Transcribe the audio and translate the result to Malayalam.
Return only the translated text, no explanations.
```

### Language Detection
- Gemini detects source language automatically
- Display detected language name in the translation message header
- Primary expected source: Arabic, but supports any language Gemini handles

## Message Formatting

### Progress Messages (edited in place)
- "📥 Downloading..."
- "🌐 Translating from [Language]..."
- "🎙️ Generating audio..."

### Translation Text Message
```
[Language Flag] [Language] → മലയാളം
📱 Instagram [Content Type] by @[author]

[Translated Malayalam text]
```

For audio-only (no caption):
```
🎙️ Audio transcribed and translated

[Translated Malayalam text]
```

### Voice Selection
- Keep `en-US-AvaMultilingualNeural` (multilingual voice, handles Malayalam text)

## Error Handling

| Failure | Response |
|---------|----------|
| yt-dlp can't extract (private/expired/unsupported) | "Couldn't extract content from this link. Try a public post/reel." |
| No caption AND no audio | "This post doesn't have text or audio to translate." |
| Gemini returns empty translation | "Translation service is busy. Please try again." |
| Audio too large for Gemini upload | "Audio is too large. Try a shorter clip." |
| TTS fails but translation succeeded | Send text, then: "Audio generation failed, but here's the translation above." |
| Long caption (>4096 chars) | Split into multiple Telegram messages |

## Rate Limiting
- Simple cooldown: 1 request per 30 seconds per chat_id
- Rejects with: "Please wait before sending another request."

## Dependencies
- No new dependencies. Uses existing: yt-dlp, google-genai, edge-tts, fastapi, requests

## Files Changed
- `instagram.py` — new
- `translate.py` — refactored into independent functions
- `main.py` — updated flow with progress messages and text-first delivery
- `prompt.txt` — updated translation prompts
