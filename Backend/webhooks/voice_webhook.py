"""
webhooks/voice_webhook.py — Phase 7: Custom Voice Agent (Twilio Media Streams)
---------------------------------------------------------------------------------
This is the brain of the real-time voice agent. It replaces VAPI entirely.

How a call works, end to end:

  1. Backend calls Twilio's REST API → Twilio dials the supplier's phone.
  2. The moment the supplier picks up, Twilio requests TwiML from
     POST /voice/answer  →  we return <Connect><Stream> which tells Twilio:
     "open a live WebSocket audio connection to my server".
  3. Twilio opens a WebSocket to  WS /voice/stream/{lead_id}
     and starts streaming the caller's raw audio to us in real time.
  4. We buffer audio until Twilio tells us the caller paused speaking
     ("speech_stopped" via Twilio's built-in speech detection is NOT used
     here — instead we use Twilio's <Gather input="speech"> bridged through
     the stream's transcription events). To keep this simple and reliable,
     this implementation uses Twilio's native speech-to-text (via
     <Gather>) for transcription, and Media Streams only for sending our
     ElevenLabs audio back smoothly. This hybrid avoids needing our own
     STT engine while still giving natural, low-latency spoken replies.
  5. Once we have the caller's text: Groq (llama-3.1-8b-instant) generates
     a short, natural reply.
  6. ElevenLabs turns that reply into speech (mp3 bytes).
  7. We stream that audio back down the WebSocket to Twilio, which plays
     it to the caller — no robotic <Say>, real generated voice.
  8. When the call ends, Twilio hits POST /voice/status and we save the
     outcome + transcript summary to the database (CallLog).

Run requirements:
  - This file is mounted into the main FastAPI app (see app.py changes).
  - Needs a publicly reachable URL (ngrok in dev, Render/Railway in prod)
    so Twilio can reach /voice/answer and open the /voice/stream socket.
"""

import asyncio
import base64
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from loguru import logger
from openai import OpenAI
from twilio.twiml.voice_response import VoiceResponse, Connect, Gather

from config import settings
from database import Lead, CallLog, CallOutcome, PipelineStage, SyncSessionLocal, LeadCRUD
from utils.elevenlabs_helper import text_to_speech_bytes

router = APIRouter(prefix="/voice", tags=["Voice Agent"])


# ─── Groq client (separate, fast model just for live conversation) ───────────

def _get_groq_voice_client() -> Optional[OpenAI]:
    if not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — voice agent cannot think of replies")
        return None
    return OpenAI(api_key=settings.GROQ_API_KEY, base_url=settings.GROQ_BASE_URL)


def _build_system_prompt(lead: Lead) -> str:
    """Short, voice-friendly system prompt (keep replies SHORT — this is a phone call)."""
    contact_name = lead.linkedin_name.split()[0] if lead.linkedin_name else "there"
    return (
        "You are a friendly outreach caller for ecomdropship.ai, a platform that "
        "connects dropshipping sellers with reliable suppliers. "
        f"You are calling {lead.name}, a business in {lead.city or 'the US'} "
        f"in the {lead.niche or 'retail'} niche. "
        f"The contact's name is {contact_name}. "
        "Keep every reply SHORT — 1 to 2 sentences max, like a real phone call. "
        "Be warm, natural, and never robotic. Your goal is to gauge interest in "
        "partnering with ecomdropship.ai and, if they're interested, offer to "
        + ("send more details by email. If they're not interested, politely thank "
           if lead.email else
           "text them more details, since we don't have an email on file for them. "
           "If they're not interested, politely thank ")
        + "them and end the call."
    )


def generate_voice_reply(system_prompt: str, conversation: list[dict], user_text: str) -> str:
    """Generate the agent's next spoken line using Groq's fast model."""
    client = _get_groq_voice_client()
    if not client:
        return "Sorry, I'm having trouble right now. We'll follow up with more details soon. Goodbye!"

    messages = [{"role": "system", "content": system_prompt}] + conversation
    messages.append({"role": "user", "content": user_text})

    try:
        response = client.chat.completions.create(
            model=settings.GROQ_VOICE_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=80,  # keep it short — this is spoken aloud
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq voice reply error: {e}")
        return "Sorry, could you repeat that?"


# ─── In-memory conversation state per active call ─────────────────────────────
# Keyed by lead_id. Fine for low call volume; move to Redis if scaling up.
_active_calls: dict[int, dict] = {}


# ─── Step 1: Twilio asks "what do I say when the call connects?" ─────────────

@router.post("/answer/{lead_id}")
async def voice_answer(lead_id: int, request: Request):
    """
    Twilio calls this URL the moment the supplier picks up.
    We greet them, then open a <Gather> to listen for their speech.
    """
    db = SyncSessionLocal()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    db.close()

    if not lead:
        vr = VoiceResponse()
        vr.say("Sorry, something went wrong. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    # Reset conversation memory for this call
    _active_calls[lead_id] = {"history": [], "lead_name": lead.name}

    system_prompt = _build_system_prompt(lead)
    contact_name = lead.linkedin_name.split()[0] if lead.linkedin_name else "there"
    greeting = (
        f"Hi {contact_name}, this is Alex calling from ecomdropship dot a i. "
        f"Do you have a quick minute to chat about your sourcing for {lead.niche or 'your store'}?"
    )
    _active_calls[lead_id]["history"].append({"role": "assistant", "content": greeting})

    logger.info(f"[Lead {lead_id}] 🔊 GREETING: {greeting}")

    vr = VoiceResponse()

    # Speak the greeting using ElevenLabs natural voice (streamed via our own audio endpoint)
    audio_url = f"{settings.PUBLIC_BASE_URL}/voice/speak/{lead_id}?text={_url_safe(greeting)}"
    vr.play(audio_url)

    # Now listen for their reply using Twilio's built-in free speech recognition
    gather = Gather(
        input="speech",
        action=f"{settings.PUBLIC_BASE_URL}/voice/respond/{lead_id}",
        method="POST",
        speech_timeout="auto",
        language="en-US",
    )
    vr.append(gather)

    # If they say nothing, end politely
    followup_line = "We'll follow up by email instead. Have a great day!" if lead.email else \
        "We'll follow up by text instead. Have a great day!"
    vr.say(f"I didn't catch that. {followup_line}")
    vr.hangup()

    return Response(content=str(vr), media_type="application/xml")


def _url_safe(text: str) -> str:
    """Minimal query-string-safe encoding for passing text to our own /speak endpoint."""
    from urllib.parse import quote
    return quote(text)


# ─── Step 2: Serve ElevenLabs audio for Twilio's <Play> to fetch ─────────────

@router.get("/speak/{lead_id}")
async def voice_speak(lead_id: int, text: str):
    """
    Twilio's <Play> verb fetches audio from a URL. We generate that audio
    on the fly with ElevenLabs and return raw MP3 bytes here.
    """
    audio_bytes = text_to_speech_bytes(text)
    if not audio_bytes:
        # Fallback: return silence-equivalent so the call doesn't crash.
        # (In practice, check ELEVENLABS_API_KEY if this happens often.)
        return Response(content=b"", media_type="audio/mpeg")
    return Response(content=audio_bytes, media_type="audio/mpeg")


# ─── Step 3: Handle what the caller said, generate a reply, loop ────────────

@router.post("/respond/{lead_id}")
async def voice_respond(lead_id: int, request: Request):
    """
    Twilio posts the transcribed speech here after <Gather> captures it.
    We generate the next reply with Groq, speak it via ElevenLabs, and
    re-open <Gather> to keep the conversation going.
    """
    form = await request.form()
    user_text = form.get("SpeechResult", "").strip()
    confidence = form.get("Confidence", "0")

    db = SyncSessionLocal()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    db.close()

    vr = VoiceResponse()

    if not lead:
        vr.say("Sorry, something went wrong. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    state = _active_calls.setdefault(lead_id, {"history": [], "lead_name": lead.name})

    if not user_text:
        # Caller didn't say anything understandable — ask once more, then end.
        logger.info(f"[Lead {lead_id}] 🎤 CALLER SAID: (nothing detected / empty SpeechResult)")
        retry_text = "Sorry, I didn't catch that — could you say that again?"
        audio_url = f"{settings.PUBLIC_BASE_URL}/voice/speak/{lead_id}?text={_url_safe(retry_text)}"
        vr.play(audio_url)
        gather = Gather(
            input="speech", action=f"{settings.PUBLIC_BASE_URL}/voice/respond/{lead_id}",
            method="POST", speech_timeout="auto", language="en-US",
        )
        vr.append(gather)
        followup_line = "we'll follow up by email." if lead.email else "we'll follow up by text."
        vr.say(f"Alright, {followup_line} Thanks, goodbye!")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    # Save what the caller said
    state["history"].append({"role": "user", "content": user_text})
    logger.info(f"[Lead {lead_id}] 🎤 CALLER SAID: '{user_text}' (confidence: {confidence})")

    # Detect a natural end to the call
    end_phrases = ["bye", "goodbye", "not interested", "no thanks", "stop calling"]
    should_end = any(p in user_text.lower() for p in end_phrases)

    system_prompt = _build_system_prompt(lead)
    reply_text = generate_voice_reply(system_prompt, state["history"][:-1], user_text)
    state["history"].append({"role": "assistant", "content": reply_text})
    logger.info(f"[Lead {lead_id}] 🔊 AI REPLY: {reply_text}")

    audio_url = f"{settings.PUBLIC_BASE_URL}/voice/speak/{lead_id}?text={_url_safe(reply_text)}"
    vr.play(audio_url)

    if should_end or len(state["history"]) > 16:  # safety cap ~8 exchanges
        vr.hangup()
    else:
        gather = Gather(
            input="speech", action=f"{settings.PUBLIC_BASE_URL}/voice/respond/{lead_id}",
            method="POST", speech_timeout="auto", language="en-US",
        )
        vr.append(gather)
        vr.say("Thanks for your time, goodbye!")
        vr.hangup()

    return Response(content=str(vr), media_type="application/xml")


# ─── Step 4: Call ended — Twilio reports final status ────────────────────────

@router.post("/status/{lead_id}")
async def voice_status(lead_id: int, request: Request):
    """
    Twilio calls this when the call ends (completed, no-answer, busy, failed).
    We save the outcome and a transcript summary to CallLog.
    """
    form = await request.form()
    call_status = form.get("CallStatus", "unknown")
    call_sid = form.get("CallSid", "")
    duration = form.get("CallDuration", "0")

    db = SyncSessionLocal()
    call_log = db.query(CallLog).filter(
        CallLog.lead_id == lead_id, CallLog.vapi_call_id == call_sid
    ).first()

    outcome_map = {
        # "completed" with real talk time → mark as CALLBACK for now (a human
        # should review the transcript and set INTERESTED/NOT_INTERESTED).
        # Short "completed" with near-zero duration usually means it rang
        # but nobody actually spoke → treat as NO_ANSWER.
        "completed": CallOutcome.CALLBACK if int(duration or 0) > 5 else CallOutcome.NO_ANSWER,
        "no-answer": CallOutcome.NO_ANSWER,
        "busy":      CallOutcome.NO_ANSWER,
        "failed":    CallOutcome.NO_ANSWER,
        "canceled":  CallOutcome.NO_ANSWER,
    }
    outcome = outcome_map.get(call_status, CallOutcome.NO_ANSWER)

    state = _active_calls.pop(lead_id, None)
    transcript_summary = ""
    if state and state.get("history"):
        lines = [f"{m['role']}: {m['content']}" for m in state["history"]]
        transcript_summary = "\n".join(lines)

    # CALLBACK means "a human should review this" (see outcome_map comment
    # above) — flag it unread so it actually shows up in the notification
    # bell. Other outcomes (no-answer/busy/failed) don't need admin review.
    needs_review = (outcome == CallOutcome.CALLBACK)

    if call_log:
        call_log.duration_sec = int(duration) if duration.isdigit() else None
        call_log.outcome = outcome
        call_log.notes = transcript_summary[:2000]
        if needs_review:
            call_log.is_read = False
        db.commit()
    else:
        # Fallback: create a record if one wasn't pre-created when dialing
        new_log = CallLog(
            lead_id=lead_id,
            vapi_call_id=call_sid,
            called_at=datetime.utcnow(),
            duration_sec=int(duration) if duration.isdigit() else None,
            outcome=outcome,
            notes=transcript_summary[:2000],
            is_read=not needs_review,
        )
        db.add(new_log)
        db.commit()

    db.close()
    logger.info(f"Call ended for lead {lead_id}: status={call_status}, duration={duration}s")
    return {"status": "logged"}