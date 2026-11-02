"""
message_writer.py — Writes the text for one step of one lead
-------------------------------------------------------------
Uses the lead's Industry Profile (what we offer, tone, language) and what we
learned about the lead while scoring it, so the same code serves every industry.
Returns None when the AI is unavailable; nothing is made up in that case.
"""

import json
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app_settings import get_app_settings
from config import settings
from database import Lead
from profile_schema import ProfileConfig, SequenceStep
from utils.groq_helper import _call_groq
from weakness_check import split_points

OPT_OUT_LINE_EMAIL = "If this isn't relevant, just reply STOP and I won't contact you again."
OPT_OUT_LINE_WHATSAPP = "Reply STOP to opt out."


@dataclass
class Draft:
    subject: Optional[str]
    body: str


def sender_identity() -> tuple[str, str]:
    """(person name, company name) used in signatures."""
    cfg = get_app_settings()
    person = settings.EMAIL_FROM_NAME or cfg.get("adminName") or ""
    return person.strip(), (cfg.get("companyName") or "").strip()


def step_intent(steps: list[SequenceStep], step: SequenceStep) -> str:
    """first_contact | follow_up | closing, judged from the step's place in its channel."""
    same_channel = [s for s in steps if s.channel == step.channel]
    if same_channel and same_channel[0].key == step.key:
        return "first_contact"
    text = f"{step.key} {step.label}".lower()
    if "clos" in text or "last" in text or "final" in text:
        return "closing"
    return "follow_up"


def _observations(lead: Lead) -> list[str]:
    """What we noticed about the lead: its (checked) weakness, else its biggest gaps, else the rules it matched."""
    attrs = lead.attributes or {}
    fixes = {g["label"]: g.get("fix") for g in attrs.get("top_gaps", []) if g.get("label")}
    points = split_points(attrs.get("weakness") or "")
    if points:
        return [p + (f" (our matching service: {fixes[p]})" if fixes.get(p) else "") for p in points[:3]]
    gaps = [g["label"] + (f" (our matching service: {g['fix']})" if g.get("fix") else "")
            for g in (lead.attributes or {}).get("top_gaps", []) if g.get("label")]
    if gaps:
        return gaps[:3]
    rules = (lead.score_breakdown or {}).get("rules", [])
    return [r["label"] for r in rules if r.get("status") == "matched" and r.get("points", 0) > 0][:4]


PRICE_RULE = ("Never state or hint at prices, costs, discounts or dollar amounts. If price comes up, say plans are "
              "scoped per business and the exact numbers are shared on a short call. Only name a specific software "
              "product as one we work with if that exact product name appears in the facts above; otherwise say we can "
              "usually connect to the kind of tool they use and will confirm that product on a short call. Do the same "
              "for languages, features and results that are not written in the facts.")


def _knowledge_section(knowledge: str, heading: str) -> str:
    """The text under one '## heading' of the knowledge base ('' when absent)."""
    for block in knowledge.split("\n## ")[1:]:
        title, _, body = block.partition("\n")
        if title.lower().startswith(heading.lower()):
            return body.strip()
    return ""


def _service_facts(knowledge: str, limit: int = 2600) -> str:
    """For a first email: the problems we fix and the benefits; the full text is for replies."""
    sections = [(name, _knowledge_section(knowledge, name)) for name in ("The problems we fix", "Benefits for")]
    return "\n\n".join(f"{name}:\n{body}" for name, body in sections if body)[:limit]


_HONORIFICS = {"dr", "mr", "mrs", "ms", "miss", "prof"}


def _first_name(lead: Lead) -> Optional[str]:
    """What to call the contact: 'Dana', or 'Dr. Lee' when the name starts with a title."""
    words = (lead.linkedin_name or "").split()
    if not words:
        return None
    if words[0].rstrip(".").lower() in _HONORIFICS and len(words) > 1:
        return f"{words[0].rstrip('.').capitalize()}. {words[-1]}"        # title + surname: "Dr. Chris Lee" -> "Dr. Lee"
    return words[0]


def _clean_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip(), strict=False)


def write_message(lead: Lead, step: SequenceStep, steps: list[SequenceStep], config: ProfileConfig,
                  previous: list[str]) -> Optional[Draft]:
    """
    previous: the texts already sent to this lead, oldest first, so a follow-up
    does not repeat them.
    """
    offer = config.offer
    person, company = sender_identity()
    intent = step_intent(steps, step)
    if previous and intent == "first_contact":
        intent = "re_engage"           # we wrote to them before (they said "not now"): do not start from scratch
    is_email = step.channel == "email"

    guidance = {
        "re_engage": "We wrote to this person some time ago and they said it was not the right time. Reconnect "
                     "briefly, without repeating the earlier messages, and ask one easy question.",
        "first_contact": "This is the first message this person receives from us. Introduce the idea briefly "
                         "and ask one easy question.",
        "follow_up": "This is a follow-up. Do not repeat the earlier messages; add one new, useful angle and "
                     "keep it light.",
        "closing": "This is the last message. Be polite, acknowledge they may be busy, and leave the door open.",
    }[intent]
    length = ("Email body: 70 to 130 words." if intent in ("first_contact", "re_engage") else "Email body: 40 to 90 words.") \
        if is_email else "WhatsApp message: at most 280 characters, conversational, no subject line."

    system_prompt = (
        f"You write outreach messages on behalf of {person or 'a team member'}"
        f"{' at ' + company if company else ''}.\n"
        f"What we offer: {offer.summary or 'a service that helps local businesses'}\n"
        + (f"Facts about our service you may use (from our website):\n{_service_facts(offer.knowledge)}\n" if offer.knowledge else "")
        + f"{PRICE_RULE}\n"
        f"Tone: {offer.tone}. Write in this language: {offer.language}.\n"
        "Rules: plain text only, no links, no emojis, no placeholders like [Name], never invent facts "
        "or numbers, never claim to have visited their premises. Do not write a signature or an "
        "opt-out line; they are added automatically.\n"
        "The 'things we noticed' come from an automatic look at their website, so word them as what you "
        "could not find (for example: \"I couldn't find a way to book online on your site\"), never as certain "
        "facts. Mention at most two, and tie them to a lost call, booking or follow-up that the offer fixes.\n"
        + ("Return ONLY JSON: {\"subject\": \"...\", \"body\": \"...\"}" if is_email
           else "Return ONLY the message text.")
    )

    notes = _observations(lead)
    user_prompt = (
        f"Business: {lead.name}\nCity: {lead.city or 'unknown'}\nIndustry: {lead.niche or 'unknown'}\n"
        f"Contact first name: {_first_name(lead) or 'unknown (use a neutral greeting)'}\n"
        f"Things we noticed: {'; '.join(notes) if notes else 'nothing specific'}\n"
        f"Step: {step.label or step.key} ({intent.replace('_', ' ')})\n"
        f"{guidance}\n{length}\n"
        + (f"Messages already sent to them:\n- " + "\n- ".join(p[:300] for p in previous) + "\n" if previous else "")
    )

    raw = _call_groq(system_prompt=system_prompt, user_prompt=user_prompt,
                     max_tokens=500, temperature=0.6)
    if not raw:
        return None

    try:
        if is_email:
            data = _clean_json(raw)
            subject, body = str(data.get("subject", "")).strip(), str(data.get("body", "")).strip()
            if not subject or not body:
                return None
            footer = "\n\n".join(x for x in (person, company) if x)
            return Draft(subject, "\n\n".join(x for x in (body, footer, OPT_OUT_LINE_EMAIL) if x))

        body = raw.strip().strip('"')
        if not body:
            return None
        if intent in ("first_contact", "re_engage"):
            body = f"{body}\n\n{OPT_OUT_LINE_WHATSAPP}"
        return Draft(None, body)

    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Could not read the AI's message for lead {lead.id}: {e}")
        return None


def write_reply(lead: Lead, channel: str, their_text: str, label: str, config: ProfileConfig,
                previous: list[str]) -> Optional[Draft]:
    """
    An answer to what the lead wrote. Only facts from the profile's offer and FAQ may be used;
    anything else is promised to be confirmed. The subject (email) is set by the caller.
    """
    offer = config.offer
    person, company = sender_identity()
    is_email = channel == "email"

    goal = {
        "interested": "They are interested. Thank them, explain in two or three sentences what we do and how it "
                      + (f"works, and invite them to book a short call here: {offer.booking_link}"
                         if offer.booking_link else "works, and ask when a short call would suit them."),
        "question": "Answer their question using only the offer summary and FAQ below. If the answer is not "
                    "there, say you will confirm it and ask when a short call would suit them.",
        "not_now": "They said the timing is not right. Thank them, say there is no pressure and that you will "
                   "check back in a while. Do not try to persuade them.",
    }.get(label, "Reply helpfully and briefly.")
    length = "Email body: 40 to 100 words." if is_email else "WhatsApp message: at most 320 characters."

    system_prompt = (
        f"You answer a lead's reply on behalf of {person or 'a team member'}"
        f"{' at ' + company if company else ''}.\n"
        f"What we offer: {offer.summary or 'a service that helps local businesses'}\n"
        f"FAQ:\n{offer.faq or '(none)'}\n"
        + (f"Knowledge base (answer only from this and the FAQ):\n{offer.knowledge}\n" if offer.knowledge else "")
        + f"{PRICE_RULE}\n"
        f"Tone: {offer.tone}. Write in this language: {offer.language}.\n"
        "Rules: plain text only, no emojis, no placeholders like [Name], never invent facts, prices, "
        "results or promises. Do not write a signature; it is added automatically.\n"
        + ("Return ONLY JSON: {\"body\": \"...\"}" if is_email else "Return ONLY the message text.")
    )
    user_prompt = (
        f"Business: {lead.name}\nContact first name: {_first_name(lead) or 'unknown (use a neutral greeting)'}\n"
        f"Their message:\n{their_text[:1500]}\n\n{goal}\n{length}\n"
        + (f"What we sent before:\n- " + "\n- ".join(p[:300] for p in previous[-3:]) + "\n" if previous else "")
    )

    raw = _call_groq(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=450, temperature=0.4)
    if not raw:
        return None
    try:
        if is_email:
            body = str(_clean_json(raw).get("body", "")).strip()
            if not body:
                return None
            footer = "\n".join(x for x in (person, company) if x)
            return Draft(None, "\n\n".join(x for x in (body, footer) if x))
        body = raw.strip().strip('"')
        return Draft(None, body) if body else None
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Could not read the AI's reply for lead {lead.id}: {e}")
        return None
