"""
utils/elevenlabs_helper.py — ElevenLabs Text-to-Speech Wrapper
-----------------------------------------------------------------
Converts AI-generated text replies into natural-sounding speech audio
during live voice calls.

Why ElevenLabs?
  - Most natural-sounding TTS available (vs robotic Twilio <Say>)
  - Free tier: 10,000 characters/month — enough for testing
  - Fast enough for near-real-time voice agent use

Usage:
    from utils.elevenlabs_helper import text_to_speech_bytes
    audio_bytes = text_to_speech_bytes("Hello, how can I help you today?")
"""

from typing import Optional

import requests
from loguru import logger

from config import settings


# ── Default voice ───────────────────────────────────────────────────────────
# "Adam" — a FREE, premade ElevenLabs voice (deep, clear, professional male).
# NOTE: ElevenLabs "Voice Library" voices (the ones you browse/favorite in the
# dashboard) require a PAID plan to use via the API. Free accounts can only
# use the built-in "Premade" voices like this one. Upgrade later if you want
# a specific Library voice.
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"


def text_to_speech_bytes(
    text: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = "eleven_turbo_v2_5",
) -> Optional[bytes]:
    """
    Calls ElevenLabs API to convert text into speech audio.

    Args:
        text: The text to speak (keep short — calls are real-time, latency matters)
        voice_id: Which ElevenLabs voice to use
        model_id: "eleven_turbo_v2_5" is the fastest model — best for live calls

    Returns:
        Raw MP3 audio bytes, or None if the call failed / no API key set.
    """
    if not settings.ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY not set — cannot generate speech")
        return None

    if not text or not text.strip():
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            return response.content
        else:
            logger.error(f"ElevenLabs TTS error {response.status_code}: {response.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"ElevenLabs API call failed: {e}")
        return None