"""TTS — converts text to mulaw 8kHz for Twilio. ElevenLabs primary, Edge TTS fallback."""

import asyncio
import os
import tempfile

from elevenlabs.client import ElevenLabs

# Default: Adam — works on free tier (library voices like Rachel require paid)
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"

_client: ElevenLabs | None = None


def get_elevenlabs_client() -> ElevenLabs:
    global _client
    if _client is None:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        _client = ElevenLabs(api_key=api_key)
    return _client


def _edge_tts_to_mulaw(text: str) -> bytes:
    """Fallback: Edge TTS (free, no API key) → mulaw 8kHz. Requires ffmpeg."""
    import edge_tts
    from audioop import lin2ulaw
    from pydub import AudioSegment

    async def _generate():
        communicate = edge_tts.Communicate(text.strip(), "en-US-JennyNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        try:
            await communicate.save(mp3_path)
            audio = AudioSegment.from_mp3(mp3_path)
            audio = audio.set_frame_rate(8000).set_channels(1)
            raw_pcm = audio.raw_data
            return lin2ulaw(raw_pcm, 2)
        finally:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass

    return asyncio.run(_generate())


def text_to_mulaw(text: str, voice_id: str | None = None) -> bytes:
    """
    Convert text to mulaw 8kHz audio for Twilio.
    Uses ElevenLabs; falls back to Edge TTS on failure (401, 402, etc.).
    """
    if not text or not text.strip():
        return b""

    # Try ElevenLabs first
    try:
        client = get_elevenlabs_client()
        voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID
        chunks = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text.strip(),
            model_id="eleven_multilingual_v2",
            output_format="ulaw_8000",
        )
        return b"".join(chunks)
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "402" in err_str or "payment" in err_str or "unusual" in err_str:
            print(f"⚠️  ElevenLabs failed ({e}), using Edge TTS fallback")
        else:
            print(f"⚠️  ElevenLabs error: {e}, using Edge TTS fallback")
        try:
            return _edge_tts_to_mulaw(text)
        except Exception as fallback_err:
            print(f"❌ Edge TTS fallback failed: {fallback_err}")
            print("   Install ffmpeg for fallback: brew install ffmpeg")
            return b""
