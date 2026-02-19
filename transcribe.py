"""
Transcription module for OpenClaude — converts voice messages to text.

Uses Deepgram Nova-3 Multilingual API. Requires DEEPGRAM_API_KEY.
"""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("OpenClaude.transcribe")


async def transcribe(audio_path: Path) -> str:
    """Transcribe an audio file to text using Deepgram Nova-3."""
    api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        logger.error("DEEPGRAM_API_KEY not set")
        return "[Transcription failed: DEEPGRAM_API_KEY not set]"

    try:
        from deepgram import DeepgramClient
    except ImportError:
        logger.error("deepgram-sdk is not installed. Install: pip install deepgram-sdk")
        return "[Transcription failed: deepgram-sdk not installed]"

    try:
        client = DeepgramClient(api_key=api_key)
        audio_bytes = audio_path.read_bytes()

        model = os.getenv("DEEPGRAM_MODEL", "nova-3")
        language = os.getenv("DEEPGRAM_LANGUAGE", "ru")

        def _transcribe() -> str:
            response = client.listen.v1.media.transcribe_file(
                request=audio_bytes,
                model=model,
                language=language,
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
