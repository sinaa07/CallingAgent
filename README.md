# AI Calling Agent

Voice AI phone assistant: **User speaks → AI transcribes → LLM replies → Caller hears natural voice**. Full speech-to-speech conversation loop over Twilio.

---

## Implementations Summary

### Day 1 — Twilio Media Streams + Deepgram STT

| Feature | Implementation |
|---------|----------------|
| **Twilio webhook** | `POST /voice` returns TwiML with `<Connect><Stream>` for bidirectional Media Stream |
| **WebSocket endpoint** | `wss://{host}/media-stream` receives Twilio media events |
| **Audio format** | 8 kHz mulaw (Twilio native), base64-encoded in JSON |
| **Deepgram live STT** | Real-time transcription via `AsyncDeepgramClient.listen.v1.connect()` |
| **Stream config** | `nova-2`, `en-US`, `mulaw`, `8000` Hz, interim results, VAD |

### Day 2 — LLM Integration

| Feature | Implementation |
|---------|----------------|
| **System prompt** | `app/config/prompts.py` — agent persona and use case |
| **LLM** | GPT-4o via OpenAI API; transcript → completion → reply |
| **Empty/silence** | Fallback prompt when transcript is empty (logged) |

### Day 3 — Full Speech Loop + Memory

| Feature | Implementation |
|---------|----------------|
| **ElevenLabs TTS** | `ulaw_8000` output (no conversion); 10k chars/month free tier |
| **Stream to caller** | Mulaw chunks sent via Twilio Media Stream `media` messages |
| **Opening greeting** | Played on stream connect (from `GREETING` in prompts) |
| **Silence fallback** | "Sorry, I didn't catch that — could you repeat?" spoken via TTS |
| **Conversation memory** | Last 5 turns (user + assistant) passed to LLM each request |

---

## Architecture

```
Caller → Twilio → WebSocket /media-stream
                        ↓
              [Twilio media events] → base64 mulaw
                        ↓
              Deepgram Live STT → transcript
                        ↓
              GPT-4o (with history) → text reply
                        ↓
              ElevenLabs TTS → mulaw 8kHz
                        ↓
              WebSocket media → Twilio → Caller
```

---

## Setup

### 1. Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=+1...
YOUR_PHONE_NUMBER=+1...
NGROK_URL=https://your-ngrok-url.ngrok-free.dev

# Deepgram (STT)
DEEPGRAM_API_KEY=

# OpenAI (LLM)
OPENAI_API_KEY=

# ElevenLabs (TTS, 10k chars/month free). Falls back to Edge TTS if blocked.
ELEVENLABS_API_KEY=

# Edge TTS fallback requires ffmpeg: brew install ffmpeg

PORT=8000
```

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

**Edge TTS fallback** (when ElevenLabs is blocked): install ffmpeg for audio conversion:
```bash
brew install ffmpeg   # macOS
```

### 3. Run locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. Expose with ngrok

```bash
ngrok http 8000
```

Use the ngrok HTTPS URL (e.g. `https://xxx.ngrok-free.dev`) as `NGROK_URL` in `.env`.

### 5. Configure Twilio

1. Twilio Console → Phone Numbers → your number
2. **Voice & Fax** → A call comes in → Webhook
3. URL: `https://your-ngrok-url.ngrok-free.dev/voice`
4. Method: `POST`

---

## Running Tests

### Manual test (full flow)

1. Start server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Start ngrok: `ngrok http 8000`
3. Ensure Twilio webhook points to ngrok URL
4. Call your Twilio number
5. You should hear the greeting, then speak and hear the AI reply

### Quick import check

```bash
python -c "from app.main import app; print('OK')"
```

### Optional: trigger outbound call

If `app/phone/router.py` is mounted and `/call` exists:

```bash
curl -X POST http://localhost:8000/call
```

---

## File Structure

```
app/
├── main.py          # FastAPI app, /voice, /media-stream, LLM, TTS wiring
├── tts.py            # ElevenLabs TTS → mulaw 8kHz
└── config/
    └── prompts.py   # SYSTEM_PROMPT, GREETING, FALLBACK
```

---

## Customization

| Config | File | Purpose |
|--------|------|---------|
| `SYSTEM_PROMPT` | `app/config/prompts.py` | Agent persona and behavior |
| `GREETING` | `app/config/prompts.py` | Opening message on connect |
| `FALLBACK` | `app/config/prompts.py` | Spoken when no speech detected |
| `MAX_HISTORY_TURNS` | `app/main.py` | Conversation memory size (default 5) |
| `ELEVENLABS_VOICE_ID` | `.env` | Voice ID (default: Rachel) |

---

## Milestone Checklist

- [x] User speaks → AI transcribes (Deepgram)
- [x] Transcript → LLM (GPT-4o)
- [x] LLM reply → TTS (ElevenLabs)
- [x] TTS audio → Caller (Twilio Media Stream)
- [x] Opening greeting on connect
- [x] Silence fallback spoken to caller
- [x] Conversation memory (last 5 turns)

