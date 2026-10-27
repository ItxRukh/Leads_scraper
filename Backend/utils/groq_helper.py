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
