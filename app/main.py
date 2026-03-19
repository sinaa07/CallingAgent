import os
import json
import base64
import asyncio
from collections import deque
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results
from openai import OpenAI

from app.config.prompts import FALLBACK, GREETING, SYSTEM_PROMPT
from app.config.settings import NGROK_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, YOUR_PHONE_NUMBER
from app.tts import text_to_mulaw
from twilio.rest import Client

load_dotenv()

app = FastAPI()
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
deepgram = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Conversation memory: last 5 turns (user + assistant pairs)
MAX_HISTORY_TURNS = 5
CHUNK_SIZE = 320  # ~40ms at 8kHz mulaw

# Timing: wait before acting on transcripts
SILENCE_FALLBACK_DELAY = 4.0  # seconds of silence before playing fallback
LLM_DEBOUNCE_DELAY = 1.5  # seconds after last transcript before sending to LLM


# ─── Day 1 webhook (Twilio calls this when someone dials your number) ─────
@app.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    """Twilio calls this when someone dials your number."""
    host = request.headers.get("host")
    # No <Say> — greeting is played via WebSocket on connect (Day 3)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/media-stream" />
    </Connect>
</Response>"""
    return PlainTextResponse(twiml, media_type="application/xml")


@app.api_route("/call", methods=["GET", "POST"])
def trigger_call():
    """Trigger outbound call to YOUR_PHONE_NUMBER (for testing)."""
    call = twilio_client.calls.create(
        to=YOUR_PHONE_NUMBER,
        from_=TWILIO_PHONE_NUMBER,
        url=f"{NGROK_URL}/voice",
    )
    return {"status": "calling", "sid": call.sid}


async def play_audio_to_caller(websocket: WebSocket, stream_sid: str, text: str) -> None:
    """Convert text to speech and stream to caller via Twilio."""
    if not text or not stream_sid:
        return
    try:
        loop = asyncio.get_event_loop()
        mulaw_bytes = await loop.run_in_executor(None, text_to_mulaw, text)
        if not mulaw_bytes:
            return
        for i in range(0, len(mulaw_bytes), CHUNK_SIZE):
            chunk = mulaw_bytes[i : i + CHUNK_SIZE]
            payload_b64 = base64.b64encode(chunk).decode("ascii")
            msg = {"event": "media", "streamSid": stream_sid, "media": {"payload": payload_b64}}
            await websocket.send_text(json.dumps(msg))
    except Exception as e:
        print(f"❌ TTS error: {e}")


# ─── Day 2 + Day 3 WebSocket: Twilio Media Streams + Deepgram + LLM + TTS ─
@app.get("/media-stream")
async def media_stream_info():
    """GET allowed for browser testing; real connections use WebSocket."""
    return {"message": "WebSocket endpoint. Twilio connects here when a call is active."}


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    print("📞 Call connected — media stream open")

    transcript_queue: asyncio.Queue[str | None] = asyncio.Queue()
    stream_active = True
    stream_sid: str | None = None
    tts_playing: list[bool] = [False]  # Mute STT while AI is speaking

    # Conversation memory: list of {"role": "user"|"assistant", "content": "..."}
    conversation_history: deque[dict[str, str]] = deque(maxlen=MAX_HISTORY_TURNS * 2)

    async def play_and_mute(text: str) -> None:
        """Play TTS and ignore transcripts while speaking."""
        tts_playing[0] = True
        try:
            await play_audio_to_caller(websocket, stream_sid or "", text)
        finally:
            tts_playing[0] = False

    def on_message(message):
        if not stream_active or tts_playing[0]:
            return
        if isinstance(message, ListenV1Results) and message.is_final:
            transcript = ""
            if message.channel.alternatives:
                transcript = (message.channel.alternatives[0].transcript or "").strip()
            try:
                transcript_queue.put_nowait(transcript if transcript else None)
            except asyncio.QueueFull:
                pass

    async def process_transcripts():
        nonlocal conversation_history
        llm_debounce_task: asyncio.Task | None = None
        silence_task: asyncio.Task | None = None
        pending_transcript: str | None = None

        def cancel_tasks():
            nonlocal llm_debounce_task, silence_task
            if llm_debounce_task and not llm_debounce_task.done():
                llm_debounce_task.cancel()
            if silence_task and not silence_task.done():
                silence_task.cancel()

        async def send_to_llm_after_delay():
            nonlocal pending_transcript, llm_debounce_task
            await asyncio.sleep(LLM_DEBOUNCE_DELAY)
            if not stream_active or not pending_transcript:
                return
            transcript = pending_transcript
            pending_transcript = None
            llm_debounce_task = None
            try:
                reply = await query_llm(transcript, conversation_history)
                conversation_history.append({"role": "user", "content": transcript})
                conversation_history.append({"role": "assistant", "content": reply})
                await play_and_mute(reply)
            except Exception as e:
                if stream_active:
                    print(f"❌ Transcript processing error: {e}")

        async def play_fallback_after_delay():
            nonlocal silence_task
            await asyncio.sleep(SILENCE_FALLBACK_DELAY)
            silence_task = None
            if stream_active:
                print(f"🔇 Silence — fallback after {SILENCE_FALLBACK_DELAY}s: {FALLBACK}")
                await play_and_mute(FALLBACK)

        while stream_active:
            try:
                transcript = await asyncio.wait_for(transcript_queue.get(), timeout=0.5)
                if transcript is None or not transcript:
                    if llm_debounce_task and not llm_debounce_task.done():
                        llm_debounce_task.cancel()
                        llm_debounce_task = None
                    if silence_task is None or silence_task.done():
                        silence_task = asyncio.create_task(play_fallback_after_delay())
                else:
                    if silence_task and not silence_task.done():
                        silence_task.cancel()
                        silence_task = None
                    pending_transcript = transcript
                    if llm_debounce_task and not llm_debounce_task.done():
                        llm_debounce_task.cancel()
                    llm_debounce_task = asyncio.create_task(send_to_llm_after_delay())
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                cancel_tasks()
                raise
            except Exception as e:
                if stream_active:
                    print(f"❌ Transcript processing error: {e}")

    async def twilio_to_deepgram():
        nonlocal stream_active, stream_sid
        try:
            async for message in websocket.iter_text():
                data = json.loads(message)
                event = data.get("event")

                if event == "connected":
                    print("✅ Twilio media stream connected")
                elif event == "start":
                    stream_sid = data.get("streamSid") or data.get("start", {}).get("streamSid")
                    print(f"🔁 Stream started — Call SID: {data['start']['callSid']}")
                    # Day 3: Opening greeting on connect
                    await play_and_mute(GREETING)
                elif event == "media":
                    audio_chunk = base64.b64decode(data["media"]["payload"])
                    if audio_chunk:
                        await dg_connection.send_media(audio_chunk)
                elif event == "stop":
                    print("🔴 Call ended")
                    break
        except Exception as e:
            print(f"❌ WebSocket error: {e}")
        finally:
            stream_active = False
            await dg_connection.send_close_stream()

    async with deepgram.listen.v1.connect(
        model="nova-2",
        language="en-US",
        encoding="mulaw",
        sample_rate="8000",
        channels="1",
        punctuate="true",
        interim_results="true",
        utterance_end_ms="1000",
        vad_events="true",
    ) as dg_connection:
        dg_connection.on(EventType.MESSAGE, on_message)
        print("🎙️  Deepgram live session started")

        listen_task = asyncio.create_task(dg_connection.start_listening())
        process_task = asyncio.create_task(process_transcripts())

        try:
            await twilio_to_deepgram()
        finally:
            stream_active = False
            process_task.cancel()
            try:
                await process_task
            except asyncio.CancelledError:
                pass
            listen_task.cancel()
            try:
                await listen_task
            except asyncio.CancelledError:
                pass
        print("🔒 Deepgram session closed")


async def query_llm(transcript: str, history: deque[dict[str, str]]) -> str:
    """Send transcript to GPT-4o with conversation memory. Log and return reply."""
    print(f"\n🤖 Sending to GPT-4o: '{transcript}'")
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *list(history),
            {"role": "user", "content": transcript},
        ]
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=150,
        )
        reply = response.choices[0].message.content
        print(f"💬 GPT-4o reply: {reply}\n")
        return reply
    except Exception as e:
        print(f"❌ LLM error: {e}")
        return "Sorry, I had trouble processing that."
