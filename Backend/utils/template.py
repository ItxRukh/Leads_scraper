"""
utils/template.py — Fills in {{name}}/{{business}}/{{city}}/{{niche}} in a
user-written template (Settings → Templates) with a specific lead's data.

{{sender}} is intentionally NOT handled here — the frontend already resolves
it once (to adminName/companyName) before the template is sent to the
backend, since it's the same value for every lead in a campaign. The four
placeholders here are per-lead and can only be resolved once we know which
lead we're sending to.
"""

from typing import Optional


def apply_template_vars(template: str, name: str = "", city: str = "",
                         niche: str = "", decision_maker: Optional[str] = None) -> str:
    """
    Replaces {{name}}, {{business}}, {{city}}, {{niche}} in `template`.

    {{name}} → the decision maker's first name if we found one via LinkedIn
    enrichment, otherwise the business name (never a raw "there" filler —
    a template the user wrote themselves should always look filled in).
    {{business}} → the business/lead name.
    """
    contact = decision_maker or name or ""
    first_name = contact.split()[0] if contact else (name or "there")

    return (
        (template or "")
        .replace("{{name}}", first_name)
        .replace("{{business}}", name or "")
        .replace("{{city}}", city or "")
        .replace("{{niche}}", niche or "")
    )