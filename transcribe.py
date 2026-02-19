"""
Transcription module for OpenClaude — converts voice messages to text.

Supports three backends:
  - local: uses faster-whisper (runs in thread pool to avoid blocking)
  - groq: uses Groq's Whisper API (requires GROQ_API_KEY)
  - deepgram: uses Deepgram Nova-3 Multilingual (requires DEEPGRAM_API_KEY)

Backend is selected via the WHISPER_BACKEND environment variable (default: local).
"""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("OpenClaude.transcribe")


async def transcribe(audio_path: Path) -> str:
    """Transcribe an audio file to text using the configured backend."""
    backend = os.getenv("WHISPER_BACKEND", "local").lower().strip()
    if backend == "groq":
        return await transcribe_groq(audio_path)
    if backend == "deepgram":
        return await transcribe_deepgram(audio_path)
    return await transcribe_local(audio_path)


async def transcribe_local(audio_path: Path) -> str:
    """Transcribe using faster-whisper locally (runs in a thread to avoid blocking)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error(
            "faster-whisper is not installed. "
            "Install it with: pip install faster-whisper  "
            "Or set WHISPER_BACKEND=groq to use the Groq API instead."
        )
        return "[Transcription failed: faster-whisper not installed]"

    model_size = os.getenv("WHISPER_MODEL", "base")

    def _run_whisper() -> str:
        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(audio_path), beam_size=5)
            return " ".join(segment.text.strip() for segment in segments)
        except Exception as e:
            logger.exception("faster-whisper transcription error")
            raise e

    try:
        text = await asyncio.to_thread(_run_whisper)
        if not text.strip():
            return "[Transcription produced no text]"
        return text.strip()
    except Exception as e:
        logger.error("Local transcription failed: %s", e)
        return "[Transcription failed]"


async def transcribe_groq(audio_path: Path) -> str:
    """Transcribe using Groq's Whisper API."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        logger.error("GROQ_API_KEY not set — cannot use Groq transcription backend")
        return "[Transcription failed: GROQ_API_KEY not set]"

    try:
        from groq import AsyncGroq
    except ImportError:
        logger.error(
            "groq package is not installed. "
            "Install it with: pip install groq"
        )
        return "[Transcription failed: groq package not installed]"

    try:
        client = AsyncGroq(api_key=api_key)
        with open(audio_path, "rb") as audio_file:
            transcription = await client.audio.transcriptions.create(
                file=(audio_path.name, audio_file),
                model="whisper-large-v3",
                response_format="text",
            )
        text = str(transcription).strip()
        if not text:
            return "[Transcription produced no text]"
        return text
    except Exception as e:
        logger.exception("Groq transcription failed")
        return "[Transcription failed]"


async def transcribe_deepgram(audio_path: Path) -> str:
    """Transcribe using Deepgram Nova-3 Multilingual API (deepgram-sdk v5)."""
    api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        logger.error("DEEPGRAM_API_KEY not set — cannot use Deepgram transcription backend")
        return "[Transcription failed: DEEPGRAM_API_KEY not set]"

    try:
        from deepgram import DeepgramClient
    except ImportError:
        logger.error(
            "deepgram-sdk is not installed. "
            "Install it with: pip install deepgram-sdk"
        )
        return "[Transcription failed: deepgram-sdk not installed]"

    try:
        client = DeepgramClient(api_key=api_key)
        audio_bytes = audio_path.read_bytes()

        def _transcribe() -> str:
            response = client.listen.v1.media.transcribe_file(
                request=audio_bytes,
                model="nova-3",
                language="ru",
                smart_format=True,
            )
            return response.results.channels[0].alternatives[0].transcript

        text = await asyncio.to_thread(_transcribe)
        text = text.strip() if text else ""
        if not text:
            return "[Transcription produced no text]"
        return text
    except Exception as e:
        logger.exception("Deepgram transcription failed")
        return "[Transcription failed]"
