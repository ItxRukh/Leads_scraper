"""
utils/groq_helper.py — Groq AI Wrapper
-----------------------------------------
Replaces Claude/Anthropic API with Groq's LLaMA model.

Why Groq?
  - Extremely fast inference (tokens/second much higher than OpenAI/Anthropic)
  - Free tier available
  - OpenAI-compatible API (same format, just different base URL + key)
  - LLaMA 3.1 70B is excellent for business text analysis

Two main functions:
  1. analyze_website()  → Audits a business website for scoring criteria
  2. generate_cold_email() → Writes a personalized cold email per lead

Usage:
    from utils.groq_helper import analyze_website, generate_cold_email
"""

import json
from typing import Optional

from loguru import logger
from openai import OpenAI   # Groq uses OpenAI-compatible API format

from config import settings


# ─── Groq Client Setup ────────────────────────────────────────────────────────

def _get_groq_client() -> Optional[OpenAI]:
    """
    Returns a Groq client configured with the Groq base URL.
    Groq uses the same interface as OpenAI, so we use the openai library.
    
    Returns None if GROQ_API_KEY is not set (graceful fallback).
    """
    if not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — AI features disabled")
        return None

    return OpenAI(
        api_key=settings.GROQ_API_KEY,
        base_url=settings.GROQ_BASE_URL,  # Points to Groq instead of OpenAI
    )


def _call_groq(system_prompt: str, user_prompt: str,
               max_tokens: int = 1000, temperature: float = 0.3) -> Optional[str]:
    """
    Core function — sends a prompt to Groq and returns the response text.
    
    Args:
        system_prompt: Instructions that define the AI's role/behavior
        user_prompt: The actual task/question
        max_tokens: Maximum length of response
        temperature: 0 = deterministic/precise, 1 = creative/varied
                     We use 0.3 for business analysis (consistent outputs)
    
    Returns:
        Response text string, or None if API call fails
    """
    client = _get_groq_client()
    if not client:
        return None

    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        # Extract the text from the response object
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return None


# ─── Website Analyzer ─────────────────────────────────────────────────────────

def analyze_website(business_name: str, website_text: str) -> Optional[dict]:
    """
    Uses Groq AI to analyze a business website's crawled text.
    This catches things that regex might miss — e.g. implicit dropshipping
    references, non-standard time formats.
    
    Args:
        business_name: Name of the business (for context)
        website_text: Cleaned text scraped from the website
    
    Returns:
        dict with analysis results, or None if AI is unavailable
        {
            "dropship_mentioned": bool,
            "processing_fast": bool,
            "processing_time": str or None,
            "delivery_fast": bool,
            "delivery_time": str or None,
            "sells_on_amazon": bool,
            "sells_on_etsy": bool,
            "sells_on_shopify": bool,
            "summary": str
        }
    """
    if not website_text or len(website_text.strip()) < 50:
        return None  # Not enough text to analyze

    system_prompt = """You are a business analyst helping evaluate wholesale suppliers 
for a dropshipping platform called ecomdropship.ai.

Analyze the provided website text and return ONLY a valid JSON object.
No explanation, no markdown, no code blocks — just raw JSON.

JSON structure required:
{
  "dropship_mentioned": true/false,
  "processing_fast": true/false,
  "processing_time": "exact text found or null",
  "delivery_fast": true/false,
  "delivery_time": "exact text found or null",
  "sells_on_amazon": true/false,
  "sells_on_etsy": true/false,
  "sells_on_shopify": true/false,
  "summary": "one sentence summary of the business and dropship potential"
}

Rules:
- dropship_mentioned: true if website mentions dropshipping, drop shipping, dropship program, etc.
- processing_fast: true ONLY if processing time is 1-2 days or less
- delivery_fast: true ONLY if delivery time is 4-5 days or less  
- sells_on_amazon/etsy/shopify: true if they have a store or listing on that platform
- summary: be concise and factual"""

    user_prompt = f"""Business: {business_name}

Website text (first 4000 characters):
{website_text[:4000]}

Analyze and return JSON:"""

    raw_response = _call_groq(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=500,
        temperature=0.1,  # Very low — we want consistent, factual output
    )

    if not raw_response:
        return None

    # ── Parse JSON response ────────────────────────────────────────────────
    try:
        # Sometimes the model wraps JSON in code blocks even when told not to
        # Clean it up just in case
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        result = json.loads(cleaned, strict=False)
        logger.debug(f"Groq analysis for {business_name}: dropship={result.get('dropship_mentioned')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Groq JSON for {business_name}: {e}\nRaw: {raw_response[:200]}")
        return None


def answer_questions(business_name: str, website_text: str,
                     questions: dict[str, str]) -> dict[str, Optional[bool]]:
    """
    Asks the AI several yes/no questions about a website in ONE call.

    Args:
        questions: {rule_key: question text}
    Returns:
        {rule_key: True | False | None}. None means the question could not be
        answered (no AI key, too little text, or an unreadable reply).
    """
    answers: dict[str, Optional[bool]] = {key: None for key in questions}
    if not questions or not website_text or len(website_text.strip()) < 50:
        return answers

    listing = "\n".join(f'- "{key}": {question}' for key, question in questions.items())
    system_prompt = (
        "You are a careful business analyst. Read the website text and answer each "
        "yes/no question about the business. Return ONLY a valid JSON object that maps "
        "each key to true or false. No explanation, no markdown. If the text gives no "
        "evidence for a question, answer false."
    )
    user_prompt = (
        f"Business: {business_name}\n\nQuestions:\n{listing}\n\n"
        f"Website text (first 4000 characters):\n{website_text[:4000]}\n\nJSON answer:"
    )

    raw = _call_groq(system_prompt=system_prompt, user_prompt=user_prompt,
                     max_tokens=300, temperature=0.0)
    if not raw:
        return answers

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        parsed = json.loads(cleaned.strip(), strict=False)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Groq answers for {business_name}: {e}")
        return answers

    if isinstance(parsed, dict):
        for key in questions:
            if isinstance(parsed.get(key), bool):
                answers[key] = parsed[key]
    return answers


# ─── Cold Email Generator ─────────────────────────────────────────────────────

def generate_cold_email(lead: dict) -> dict:
    """
    Generates a personalized cold email for a lead using Groq AI.
    
    Personalization is based on:
      - Business name, city, niche
      - Whether they mention dropshipping on their site
      - Whether they sell on Amazon/Etsy/Shopify
      - Decision maker name (from LinkedIn)

    IMPORTANT: The AI only writes the persuasive opening/body text. The
    signature is appended in code (not by the AI).
    
    Args:
        lead: dict with lead data (name, city, niche, linkedin_name, etc.)
    
    Returns:
        {"subject": str, "body": str}
        Returns empty strings if generation fails
    """
    business_name      = lead.get("name", "there")
    city               = lead.get("city", "")
    niche              = lead.get("niche", "products")
    linkedin_name      = lead.get("linkedin_name", "")
    dropship_mentioned = lead.get("dropship_mentioned", False)
    sells_marketplace  = lead.get("sells_on_marketplace", False)
    final_score        = lead.get("final_score", 0)

    # Build personalization notes for the AI
    personalization_notes = []
    if dropship_mentioned:
        personalization_notes.append(
            "They already mention dropshipping on their website — reference this directly."
        )
    if sells_marketplace:
        personalization_notes.append(
            "They sell on Amazon/Etsy/Shopify — mention that our platform is a natural next channel."
        )
    if linkedin_name:
        personalization_notes.append(
            f"Address the email to {linkedin_name} by name."
        )

    personalization_str = "\n".join(personalization_notes) if personalization_notes else \
        "Use a general but warm opening — we don't know their specific situation yet."

    system_prompt = """You are an expert cold email copywriter for ecomdropship.ai — 
a dropshipping platform that helps wholesale suppliers and small factories double 
their revenue.

Value proposition:
- Suppliers currently selling wholesale at $4/unit can sell at $8/unit on dropship model
- ecomdropship.ai handles the storefront, marketing, and customer acquisition
- Supplier only needs to fulfill orders (ship directly to customers)
- No upfront cost to join — pure revenue upside

Email style rules:
- Conversational but professional
- Short paragraphs (2-3 lines max)
- No buzzwords or corporate speak
- Include ONE specific revenue number or hook (e.g. "suppliers in [niche] typically earn 40-60% more")
- End the body with one clear, low-pressure call to action, such as a short
  question inviting them to reply. Do NOT include any URL or link.
- Do NOT write a sign-off/signature (e.g. "Best, [Name]") — that is appended
  automatically after your text.
- Total body length: 120-160 words (excluding the link/signature that gets
  appended afterward)
- Subject line: short, specific, curiosity-driven (NOT clickbait)

Return ONLY valid JSON:
{
  "subject": "email subject line here",
  "body": "email body text here (use \\n for line breaks)"
}"""

    user_prompt = f"""Write a cold email for this lead:

Business Name: {business_name}
City: {city}
Niche: {niche}
Lead Score: {final_score}/145

Personalization notes:
{personalization_str}

Return JSON with subject and body:"""

    raw_response = _call_groq(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=600,
        temperature=0.7,  # Slightly creative for email copy
    )

    if not raw_response:
        return {"subject": "", "body": ""}

    try:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        result = json.loads(cleaned, strict=False)
        ai_subject = result.get("subject", "")
        ai_body    = result.get("body", "")

        if not ai_subject or not ai_body:
            return {"subject": "", "body": ""}

        # ── Append the professional signature in code ───────────────────────
        full_body = (
            f"{ai_body}\n\n"
            f"Best regards,\n"
            f"The ecomdropship.ai Team"
        )

        logger.debug(f"Email generated for {business_name}: subject={ai_subject[:50]!r}")
        return {
            "subject": ai_subject,
            "body": full_body,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse email JSON for {business_name}: {e}")
        return {"subject": "", "body": ""}


# ─── WhatsApp Message Generator ───────────────────────────────────────────────

def generate_whatsapp_message(lead: dict, context: str = "email_followup") -> str:
    """
    Generates a short, casual WhatsApp follow-up message.
    
    Args:
        lead: Lead data dict
        context: "email_followup" (after email opened no reply) or 
                 "call_no_answer" (after missed call)
    
    Returns:
        Message string ready to send via Twilio
    """
    name = lead.get("linkedin_name") or lead.get("name", "there")
    first_name = name.split()[0] if name else "there"

    if context == "call_no_answer":
        context_note = "We tried calling but couldn't reach you."
    else:
        context_note = "You opened our email but we haven't heard back."

    system_prompt = """Write a short WhatsApp follow-up message for a B2B outreach.
Keep it casual, friendly, under 60 words. No emojis overload (max 1).
Return ONLY the message text, nothing else."""

    user_prompt = f"""Write a WhatsApp follow-up for:
Name: {first_name}
Business: {lead.get("name", "")}
Context: {context_note}
Platform: ecomdropship.ai — we help wholesale suppliers earn 2x revenue
CTA: Open to a quick 10-min chat?

Write the message:"""

    response = _call_groq(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=150,
        temperature=0.6,
    )

    return response or f"Hey {first_name}, just following up on our message about ecomdropship.ai. Would you be open to a quick 10-min chat? 🙂"


# ─── SMS Message Generator ────────────────────────────────────────────────────

def generate_sms_message(lead: dict, context: str = "email_followup") -> str:
    """
    Generates a short, professional SMS follow-up message.

    Key differences from WhatsApp:
      - Hard 160 character limit (one SMS segment)
      - No emojis — some carriers block or garble them
      - Slightly more formal tone since SMS feels more personal/direct
      - Shorter CTA — yes/no question works best

    Args:
        lead: Lead data dict (name, linkedin_name, niche)
        context: "email_followup" (opened email, no reply, 48h passed)
                 "call_no_answer" (called but no answer, 24h passed)
                 "no_email_first_touch" (no email on file — this SMS IS the
                 first touch, not a nudge to check an email that doesn't exist)

    Returns:
        SMS-ready string, guaranteed under 160 characters
    """
    name = lead.get("linkedin_name") or lead.get("name", "there")
    first_name = name.split()[0] if name else "there"

    if context == "no_email_first_touch":
        context_note = "This is our first outreach to them — no email on file, so SMS is the only channel."
    elif context == "manual":
        context_note = "This is a one-off manual outreach message sent by the team right now."
    elif context == "call_no_answer":
        context_note = "We tried calling but couldn't reach you."
    elif context == "email_no_reply":
        context_note = "We sent you an email a few days ago but haven't heard back."
    else:
        context_note = "You opened our email but we haven't heard back."

    if context in ("no_email_first_touch", "manual"):
        system_prompt = """Write a short, direct SMS for B2B outreach.
Rules:
- Under 155 characters total (strict SMS limit)
- No emojis (some carriers block them)
- Professional but friendly tone
- Do NOT assume an email was already sent or opened — this message must
  stand on its own. Introduce the offer briefly and directly.
- End with a simple yes/no question that invites a reply by SMS.
Return ONLY the SMS text, nothing else."""

        user_prompt = f"""Write an SMS for:
Name: {first_name}
Business: {lead.get("name", "")}
Context: {context_note}
Platform: ecomdropship.ai — we help wholesale suppliers earn 2x revenue
Goal: introduce the offer and ask if they'd like more details (reply yes/no)

Write the SMS (max 155 chars):"""

        response = _call_groq(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=80,
            temperature=0.5,
        )

        fallback = (
            f"Hi {first_name}, this is ecomdropship.ai — we help suppliers like "
            f"{lead.get('name', 'you')} earn 2x revenue. Interested? Reply YES for details."
        )

        result = response or fallback
        return result[:158] + ".." if len(result) > 160 else result

    system_prompt = """Write a short SMS follow-up for B2B outreach.
Rules:
- Under 155 characters total (strict SMS limit)
- No emojis (some carriers block them)
- Professional but friendly tone
- Do NOT ask them to reply to this SMS with details — SMS is just a nudge.
- The goal is to get them to check their email, where the full pitch
  lives. End with something like "Check your inbox"
  or "Full details are in your email" rather than a yes/no question.
Return ONLY the SMS text, nothing else."""

    user_prompt = f"""Write an SMS follow-up for:
Name: {first_name}
Business: {lead.get("name", "")}
Context: {context_note}
Platform: ecomdropship.ai — we help wholesale suppliers earn 2x revenue
Goal: nudge them to check the email we sent (do not ask for an SMS reply)

Write the SMS (max 155 chars):"""

    response = _call_groq(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=80,       # SMS is short — 80 tokens is plenty
        temperature=0.5,     # Less creative than WhatsApp, SMS is more formal
    )

    fallback = (
        f"Hi {first_name}, following up from ecomdropship.ai — "
        f"we sent full details to your email. Please check your inbox!"
    )

    result = response or fallback

    # Hard enforce 160 char limit
    return result[:158] + ".." if len(result) > 160 else result


# ─── VAPI Call Script Generator ───────────────────────────────────────────────

def generate_vapi_system_prompt(lead: dict) -> str:
    """
    Generates a dynamic VAPI voice agent system prompt personalized per lead.
    This is injected into the VAPI call configuration before dialing.
    
    Args:
        lead: Lead data dict with website analysis results
    
    Returns:
        Full system prompt string for the VAPI voice agent
    """
    name        = lead.get("linkedin_name") or "the owner or manager"
    business    = lead.get("name", "your business")
    city        = lead.get("city", "")
    niche       = lead.get("niche", "products")
    dropship    = lead.get("dropship_mentioned", False)
    marketplace = lead.get("sells_on_marketplace", False)

    personalization = ""
    if dropship:
        personalization += (
            f"Important: {business} already mentions dropshipping on their website. "
            "Reference this — say something like 'We noticed you're already open to dropshipping...'"
        )
    if marketplace:
        personalization += (
            f"\nImportant: {business} sells on Amazon/Etsy/Shopify already. "
            "Mention that our platform is a great additional channel alongside their existing stores."
        )

    return f"""You are a professional outreach agent calling on behalf of ecomdropship.ai.

Your goal: Introduce our platform and ask if the supplier would be open to a 15-minute demo call.

Business you are calling: {business} in {city}
Contact to ask for: {name}
Their niche: {niche}

Pitch:
- ecomdropship.ai helps wholesale suppliers earn 2x their current wholesale price
- They list their products on our platform, we handle the storefront and marketing
- They simply fulfill orders by shipping directly to end customers
- Example: A product they sell at $4 wholesale earns $8 on dropship model
- No upfront cost to join

{personalization}

Call flow:
1. Ask for {name} or the owner/manager
2. Introduce yourself briefly (keep it under 20 seconds)
3. Mention one relevant fact about their business to show you've done your homework
4. Give the value prop in 2-3 sentences
5. Ask: "Would you be open to a quick 15-minute demo to see your potential earnings?"
6. Handle objections calmly — most common ones are shipping concerns and pricing questions
7. If interested: book a call or offer to send more details by email
8. If not interested: thank them and ask if you can follow up in 3 months

Tone: Confident, friendly, not pushy. This is a conversation, not a pitch.
Keep responses SHORT — this is a phone call, not an email."""