// ─── settings.js ─────────────────────────────────────────────────────────────
// Shared utility — import getSettings() in any page to read Settings values
// ─────────────────────────────────────────────────────────────────────────────

const STORAGE_KEY = "ecomdrop_settings";

export const SETTINGS_DEFAULTS = {
  // General
  companyName:   "EcomDrop.ai",
  adminName:     "",
  timezone:      "UTC",

  // Scraper
  defaultCity:   "",
  defaultNiche:  "",
  maxLeads:      50,
  autoAudit:     true,

  // Email template
  emailSubject:  "Grow your ecom store with EcomDrop",
  emailBody:     "Hi {{name}},\n\nI came across {{business}} and wanted to reach out...\n\nWould love to connect!\n\nBest,\n{{sender}}",

  // WhatsApp template
  waTemplate:    "Hi {{name}}, this is {{sender}} from EcomDrop. I wanted to follow up on my earlier email regarding {{business}}. Would love to chat!",

  // Outreach defaults
  emailDelay:    3,
  maxEmailsDay:  100,

  // Lead scoring
  hotThreshold:  70,
  warmThreshold: 40,
  autoTier:      true,

  // Export
  exportFormat:  "csv",
  autoExport:    false,
};

/** Read merged settings (defaults + user overrides) */
export function getSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...SETTINGS_DEFAULTS, ...JSON.parse(raw) } : { ...SETTINGS_DEFAULTS };
  } catch {
    return { ...SETTINGS_DEFAULTS };
  }
}

/**
 * Replace {{name}}, {{business}}, {{city}}, {{niche}}, {{sender}} in a template.
 * Pass only the keys you have — missing keys stay as-is.
 */
export function fillTemplate(template, vars = {}) {
  return template.replace(/\{\{(\w+)\}\}/g, (_, key) =>
    vars[key] !== undefined ? vars[key] : `{{${key}}}`
  );
}

/**
 * Format an ISO date string in the timezone chosen on Settings → General
 * (falls back to UTC / the browser default if the saved zone is invalid).
 * Used by CallLogs, EmailOutreach, WhatsApp, Pipeline, and DNC so every
 * timestamp shown in the app reflects the same admin-selected timezone.
 */
export function fmtDate(iso, opts = {}) {
  if (!iso) return "—";
  const { timezone } = getSettings();
  try {
    return new Date(iso).toLocaleDateString("en-US", { timeZone: timezone, ...opts });
  } catch {
    // Invalid/unsupported IANA zone string — fall back to no explicit zone
    return new Date(iso).toLocaleDateString("en-US", opts);
  }
}