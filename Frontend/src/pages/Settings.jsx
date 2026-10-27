import { useState, useEffect, useCallback, useRef } from "react";
import "./Settings.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const STORAGE_KEY = "ecomdrop_settings";

// Fields the backend actually acts on (scraper cap, auto-audit, lead
// scoring thresholds, export defaults) — these live in Backend/data/app_settings.json
// so they take effect immediately on the next scrape/audit, no restart needed.
const BACKEND_FIELDS = [
  "companyName", "adminName", "maxLeads", "autoAudit",
  "hotThreshold", "warmThreshold", "exportFormat", "autoExport",
];

const DEFAULTS = {
  companyName:   "EcomDrop.ai",
  adminName:     "",
  defaultCity:   "",
  defaultNiche:  "",
  maxLeads:      50,
  autoAudit:     true,
  emailSubject:  "Grow your ecom store with EcomDrop",
  emailBody:     "Hi {{name}},\n\nI came across {{business}} and wanted to reach out...\n\nWould love to connect!\n\nBest,\n{{sender}}",
  smsTemplate:   "Hi {{name}}, this is {{sender}} from EcomDrop. Following up on my earlier email about {{business}}. Open to a quick 10-min chat?",  // waTemplate → smsTemplate
  emailDelay:    3,
  maxEmailsDay:  100,
  hotThreshold:  70,
  warmThreshold: 40,
  exportFormat:  "csv",
  autoExport:    false,
};

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    const saved = JSON.parse(raw);
    // Migrate old waTemplate to smsTemplate
    if (saved.waTemplate && !saved.smsTemplate) {
      saved.smsTemplate = saved.waTemplate;
      delete saved.waTemplate;
    }
    return { ...DEFAULTS, ...saved };
  } catch { return { ...DEFAULTS }; }
}

function save(data) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

// Pulls the backend-tracked fields (scoring thresholds, scraper cap, etc.)
// so they stay in sync even if changed from another tab/device.
async function fetchBackendSettings() {
  try {
    const res = await fetch(`${API}/settings`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function pushBackendSettings(cfg) {
  const payload = {};
  BACKEND_FIELDS.forEach((k) => { payload[k] = cfg[k]; });
  try {
    await fetch(`${API}/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("Failed to sync settings to backend:", e);
  }
}

// Export getSettings so other pages (SMS.jsx, EmailOutreach.jsx, Leads.jsx)
// can read it synchronously from localStorage without an extra fetch.
export function getSettings() {
  return load();
}

function Toggle({ checked, onChange }) {
  return (
    <label className="st-toggle">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span className="st-track"><span className="st-thumb" /></span>
    </label>
  );
}

function Slider({ value, min, max, step = 1, onChange, color }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="st-slider-wrap">
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="st-slider"
        style={{ "--pct": `${pct}%`, "--clr": color || "var(--or)" }} />
      <span className="st-slider-val" style={{ color: color || "var(--or)" }}>{value}</span>
    </div>
  );
}

function Section({ icon, color, bg, title, sub, children }) {
  return (
    <div className="st-section">
      <div className="st-sec-head">
        <div className="st-sec-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
        <div>
          <div className="st-sec-title">{title}</div>
          {sub && <div className="st-sec-sub">{sub}</div>}
        </div>
      </div>
      <div className="st-sec-body">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div className="st-field">
      <div className="st-field-left">
        <div className="st-field-label">{label}</div>
        {hint && <div className="st-field-hint">{hint}</div>}
      </div>
      <div className="st-field-right">{children}</div>
    </div>
  );
}

function VarChips({ vars, onInsert }) {
  return (
    <div className="st-var-chips">
      <span className="st-var-label">Insert variable:</span>
      {vars.map(v => (
        <button key={v} className="st-var-chip" onClick={() => onInsert(v)}>{`{{${v}}}`}</button>
      ))}
    </div>
  );
}

function Toast({ show }) {
  return (
    <div className={"st-toast" + (show ? " st-toast--show" : "")}>
      <i className="ti ti-check" /> Settings saved successfully
    </div>
  );
}

// ─── Niche list (same list used on the Leads page) ────────────────────────────
const NICHES = [
  "Toys & Games", "Gadgets & Electronics", "Furniture & Home Decor",
  "Kitchen & Cooking", "Baby & Kids", "Pet Supplies", "Health & Wellness",
  "Beauty & Skincare", "Hair Care", "Supplements & Vitamins",
  "Sports & Fitness", "Outdoor & Camping", "Cycling", "Yoga & Pilates",
  "Clothing & Apparel", "Shoes & Footwear", "Bags & Luggage", "Jewelry & Accessories",
  "Watches", "Sunglasses & Eyewear", "Office Supplies", "Stationery",
  "Books & Educational", "Art & Craft Supplies", "Musical Instruments",
  "Photography & Camera", "Phone Accessories", "Computer Accessories",
  "Gaming & eSports", "Smart Home", "Lighting & Lamps", "Bedding & Pillows",
  "Bathroom Accessories", "Cleaning Supplies", "Garden & Plants",
  "Tools & Hardware", "Automotive Accessories", "Car Care", "Motorcycle Gear",
  "Boat & Marine", "Fishing & Hunting", "Golf", "Swimming & Water Sports",
  "Winter Sports & Skiing", "Travel Accessories", "Party Supplies",
  "Wedding & Events", "Seasonal & Holiday", "Food & Gourmet",
  "Coffee & Tea", "Wine & Spirits", "Organic & Natural Products",
  "Eco & Sustainable Products", "Zero Waste & Reusable",
  "Men's Grooming", "Women's Fashion", "Plus Size Fashion", "Lingerie",
  "Activewear", "Swimwear", "Maternity", "Senior Care Products",
  "Medical & Mobility Aids", "First Aid & Safety", "Eye Care",
  "Dental Care", "Mental Wellness", "Essential Oils & Aromatherapy",
  "Candles & Home Fragrance", "Wall Art & Prints", "Collectibles & Figures",
  "Board Games & Puzzles", "Hobby & Model Kits", "Science & STEM Toys",
  "Woodworking", "Sewing & Knitting", "3D Printing Supplies",
  "Security & Surveillance", "Solar & Renewable Energy",
  "Wearable Tech", "VR & AR Accessories", "Drones",
  "Anime & Pop Culture Merch", "Fan & Sports Merchandise",
];

// ─── CitySearch component (city autocomplete via Open-Meteo geocoding) ────────
function CitySearch({ value, onChange, placeholder = "City..." }) {
  const [query, setQuery]     = useState(value ?? "");
  const [results, setResults] = useState([]);
  const [open, setOpen]       = useState(false);
  const [loading, setLoading] = useState(false);
  const debounceRef           = useRef(null);
  const wrapRef               = useRef(null);

  useEffect(() => { setQuery(value ?? ""); }, [value]);

  useEffect(() => {
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const search = useCallback(async (q) => {
    if (!q || q.length < 2) { setResults([]); setOpen(false); return; }
    setLoading(true);
    try {
      const res = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=8&language=en&format=json`
      );
      if (!res.ok) throw new Error("geo error");
      const data = await res.json();
      const items = (data.results ?? []).map((r) => ({
        label: [r.name, r.admin1, r.country].filter(Boolean).join(", "),
        city:  r.name,
      }));
      setResults(items);
      setOpen(items.length > 0);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleChange = (e) => {
    const val = e.target.value;
    setQuery(val);
    onChange(val);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(val), 300);
  };

  const handleSelect = (item) => {
    setQuery(item.label);
    onChange(item.city);
    setOpen(false);
    setResults([]);
  };

  return (
    <div className="city-search-wrap" ref={wrapRef} style={{ position: "relative" }}>
      <div style={{ position: "relative" }}>
        <input
          className="filter-input"
          placeholder={placeholder}
          value={query}
          onChange={handleChange}
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
          onFocus={() => results.length > 0 && setOpen(true)}
          autoComplete="off"
          style={{ paddingRight: loading ? 28 : undefined }}
        />
        {loading && (
          <i
            className="ti ti-loader-2 spin"
            style={{
              position: "absolute", right: 9, top: "50%",
              transform: "translateY(-50%)", fontSize: 13,
              color: "rgba(255,255,255,0.3)", pointerEvents: "none",
            }}
          />
        )}
      </div>
      {open && results.length > 0 && (
        <ul className="city-dropdown">
          {results.map((item, i) => (
            <li key={i} className="city-option" onMouseDown={() => handleSelect(item)}>
              <i className="ti ti-map-pin" style={{ color: "#e8622a", marginRight: 6, fontSize: 12 }} />
              {item.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─── NicheSelect component (searchable niche dropdown) ────────────────────────
function NicheSelect({ value, onChange, placeholder = "All niches" }) {
  const [query, setQuery]   = useState("");
  const [open, setOpen]     = useState(false);
  const wrapRef             = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = query.trim()
    ? NICHES.filter((n) => n.toLowerCase().includes(query.toLowerCase()))
    : NICHES;

  const handleSelect = (niche) => {
    onChange(niche.toLowerCase());
    setQuery("");
    setOpen(false);
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange("");
    setQuery("");
    setOpen(false);
  };

  const displayLabel = value
    ? NICHES.find((n) => n.toLowerCase() === value) ?? value
    : placeholder;

  return (
    <div className="niche-select-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`niche-trigger filter-select ${open ? "niche-open" : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={value ? "niche-trigger-val" : "niche-trigger-ph"}>
          {displayLabel}
        </span>
        {value ? (
          <i
            className="ti ti-x niche-clear"
            onClick={handleClear}
            title="Clear"
          />
        ) : (
          <i className={`ti ti-chevron-down niche-chevron ${open ? "rotated" : ""}`} />
        )}
      </button>

      {open && (
        <div className="niche-dropdown">
          <div className="niche-search-box">
            <i className="ti ti-search" />
            <input
              autoFocus
              placeholder="Search niche..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
            />
          </div>

          <ul className="niche-list">
            <li
              className={`niche-option ${!value ? "niche-active" : ""}`}
              onMouseDown={() => { onChange(""); setQuery(""); setOpen(false); }}
            >
              <i className="ti ti-layout-grid" style={{ fontSize: 11, opacity: 0.5 }} />
              All niches
            </li>

            {filtered.length === 0 ? (
              <li className="niche-empty">No match found</li>
            ) : (
              filtered.map((n) => (
                <li
                  key={n}
                  className={`niche-option ${value === n.toLowerCase() ? "niche-active" : ""}`}
                  onMouseDown={() => handleSelect(n)}
                >
                  {n}
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  const [cfg,    setCfg]    = useState(load);
  const [toast,  setToast]  = useState(false);
  const [danger, setDanger] = useState(false);
  const [active, setActive] = useState("general");
  const [syncing, setSyncing] = useState(true);

  // On mount: pull the backend-tracked fields (scoring thresholds, scraper
  // cap, etc.) so this page reflects whatever's actually driving the pipeline
  // right now, not just what's cached in this browser's localStorage.
  useEffect(() => {
    (async () => {
      const backendCfg = await fetchBackendSettings();
      if (backendCfg) {
        setCfg((c) => {
          const merged = { ...c, ...backendCfg };
          save(merged);
          return merged;
        });
      }
      setSyncing(false);
    })();
  }, []);

  useEffect(() => { save(cfg); }, [cfg]);

  const set = (key, val) => setCfg(c => ({ ...c, [key]: val }));

  const handleSave = () => {
    save(cfg);
    pushBackendSettings(cfg);
    setToast(true);
    setTimeout(() => setToast(false), 2800);
  };

  const handleReset = () => {
    const fresh = { ...DEFAULTS };
    setCfg(fresh);
    pushBackendSettings(fresh);
    setDanger(false);
    setToast(true);
    setTimeout(() => setToast(false), 2800);
  };

  const insertVar = (field, variable) => {
    const tag = `{{${variable}}}`;
    set(field, (cfg[field] || "") + tag);
  };

  const TABS = [
    { id: "general",   icon: "ti-settings",      label: "General"      },
    { id: "scraper",   icon: "ti-world-search",   label: "Scraper"      },
    { id: "templates", icon: "ti-template",       label: "Templates"    },
    { id: "scoring",   icon: "ti-star",           label: "Lead Scoring" },
    { id: "export",    icon: "ti-download",       label: "Export"       },
    { id: "danger",    icon: "ti-alert-triangle", label: "Danger Zone"  },
  ];

  return (
    <div className="st-page">
      <div className="st-blobs">
        <div className="st-blob st-blob--1" />
        <div className="st-blob st-blob--2" />
      </div>

      <div className="st-topbar">
        <div className="st-topbar-left">
          <div className="st-page-icon"><i className="ti ti-settings-2" /></div>
          <div>
            <h1 className="st-page-title">Settings</h1>
            <p className="st-page-sub">
              {syncing ? "Syncing with backend…" : "Scoring & scraper settings sync to the backend — templates save locally"}
            </p>
          </div>
        </div>
        <button className="st-save-btn" onClick={handleSave}>
          <i className="ti ti-device-floppy" /> Save changes
        </button>
      </div>

      <div className="st-layout">
        <nav className="st-tabs">
          {TABS.map(t => (
            <button
              key={t.id}
              className={"st-tab" + (active === t.id ? " st-tab--active" : "") + (t.id === "danger" ? " st-tab--danger" : "")}
              onClick={() => setActive(t.id)}
            >
              <i className={`ti ${t.icon}`} />
              <span>{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="st-content">

          {/* ── GENERAL ── */}
          {active === "general" && (
            <Section icon="ti-building-store" color="#e8622a" bg="rgba(232,98,42,0.13)"
              title="General" sub="Basic info about your workspace">
              <Field label="Company Name" hint="Shown across the admin panel">
                <input className="st-input" value={cfg.companyName}
                  onChange={e => set("companyName", e.target.value)} placeholder="EcomDrop.ai" />
              </Field>
              <Field label="Admin Name" hint="Your name, used in email signatures">
                <input className="st-input" value={cfg.adminName}
                  onChange={e => set("adminName", e.target.value)} placeholder="e.g. Sarah" />
              </Field>
            </Section>
          )}

          {/* ── SCRAPER ── */}
          {active === "scraper" && (
            <Section icon="ti-world-search" color="#3b82f6" bg="rgba(59,130,246,0.13)"
              title="Scraper Defaults" sub="Pre-filled values when you launch a scrape">
              <Field label="Default City" hint="Auto-filled in the scraper city field">
                <CitySearch value={cfg.defaultCity}
                  onChange={val => set("defaultCity", val)} placeholder="e.g. Miami FL" />
              </Field>
              <Field label="Default Niche" hint="Auto-filled in the scraper niche field">
                <NicheSelect value={cfg.defaultNiche}
                  onChange={val => set("defaultNiche", val)} placeholder="Select niche..." />
              </Field>
              <Field label="Max Leads Per Scrape" hint={`Currently: ${cfg.maxLeads} leads`}>
                <Slider value={cfg.maxLeads} min={10} max={500} step={10}
                  color="#3b82f6" onChange={v => set("maxLeads", v)} />
              </Field>
              <Field label="Auto-audit new leads" hint="Run quality audit automatically after each scrape">
                <Toggle checked={cfg.autoAudit} onChange={v => set("autoAudit", v)} />
              </Field>
            </Section>
          )}

          {/* ── TEMPLATES ── */}
          {active === "templates" && (
            <>
              <Section icon="ti-mail" color="#06b6d4" bg="rgba(6,182,212,0.13)"
                title="Email Template" sub="Default subject & body loaded in Email Outreach page">
                <Field label="Email Subject" hint="Subject line of your outreach email">
                  <input className="st-input" value={cfg.emailSubject}
                    onChange={e => set("emailSubject", e.target.value)}
                    placeholder="e.g. Quick question about {{business}}" />
                </Field>
                <Field label="Email Body" hint="Personalization variables are replaced automatically">
                  <div className="st-textarea-wrap">
                    <VarChips vars={["name","business","city","niche","sender"]}
                      onInsert={v => insertVar("emailBody", v)} />
                    <textarea className="st-textarea" rows={8} value={cfg.emailBody}
                      onChange={e => set("emailBody", e.target.value)}
                      placeholder="Write your email body here..." />
                  </div>
                </Field>
                <div className="st-template-note">
                  <i className="ti ti-info-circle" />
                  <span>Go to <strong>Email Outreach</strong> page → click <strong>"Load Template"</strong> to use this in a campaign</span>
                </div>
                <Field label="Email Send Delay" hint={`${cfg.emailDelay}s between each email`}>
                  <Slider value={cfg.emailDelay} min={1} max={30} step={1}
                    color="#06b6d4" onChange={v => set("emailDelay", v)} />
                </Field>
                <Field label="Max Emails Per Day" hint={`Cap at ${cfg.maxEmailsDay} emails/day`}>
                  <Slider value={cfg.maxEmailsDay} min={10} max={500} step={10}
                    color="#06b6d4" onChange={v => set("maxEmailsDay", v)} />
                </Field>
              </Section>

              {/* SMS Template — WhatsApp → SMS */}
              <Section icon="ti-message" color="#3b82f6" bg="rgba(59,130,246,0.13)"
                title="SMS Template" sub="Default message loaded in SMS page">
                <Field label="SMS Message" hint="Keep it under 160 characters for a single SMS">
                  <div className="st-textarea-wrap">
                    <VarChips vars={["name","business","sender"]}
                      onInsert={v => insertVar("smsTemplate", v)} />
                    <textarea className="st-textarea st-textarea--sm" rows={4}
                      value={cfg.smsTemplate}
                      onChange={e => set("smsTemplate", e.target.value)}
                      placeholder="Hi {{name}}, this is {{sender}}..."
                      maxLength={320}
                    />
                    <div style={{ fontSize: 11, color: cfg.smsTemplate?.length > 160 ? "#f59e0b" : "rgba(255,255,255,0.3)", marginTop: 4 }}>
                      {cfg.smsTemplate?.length ?? 0} / 160 chars
                      {cfg.smsTemplate?.length > 160 && " — will send as 2 SMS"}
                    </div>
                  </div>
                </Field>
                <div className="st-template-note">
                  <i className="ti ti-info-circle" />
                  <span>Go to <strong>SMS</strong> page → click <strong>"Load Template"</strong> to use this message</span>
                </div>
              </Section>
            </>
          )}

          {/* ── LEAD SCORING ── */}
          {active === "scoring" && (
            <Section icon="ti-star" color="#f59e0b" bg="rgba(245,158,11,0.13)"
              title="Lead Scoring" sub="Thresholds that determine hot / warm / cold tiers">
              <div className="st-tier-visual">
                <div className="st-tier-bar">
                  <div className="st-tier-seg st-tier-cold" style={{ width: `${cfg.warmThreshold}%` }}><span>Cold</span></div>
                  <div className="st-tier-seg st-tier-warm" style={{ width: `${cfg.hotThreshold - cfg.warmThreshold}%` }}><span>Warm</span></div>
                  <div className="st-tier-seg st-tier-hot" style={{ width: `${100 - cfg.hotThreshold}%` }}><span>Hot</span></div>
                </div>
                <div className="st-tier-markers">
                  <span>0</span>
                  <span style={{ marginLeft: `${cfg.warmThreshold}%` }}>{cfg.warmThreshold}</span>
                  <span style={{ marginLeft: `${cfg.hotThreshold - cfg.warmThreshold}%` }}>{cfg.hotThreshold}</span>
                  <span>100</span>
                </div>
              </div>
              <Field label="Hot Lead Threshold" hint={`Score ≥ ${cfg.hotThreshold} = 🔥 Hot`}>
                <Slider value={cfg.hotThreshold} min={cfg.warmThreshold + 5} max={95} step={5}
                  color="#e8622a" onChange={v => set("hotThreshold", v)} />
              </Field>
              <Field label="Warm Lead Threshold" hint={`Score ${cfg.warmThreshold}–${cfg.hotThreshold - 1} = 🟡 Warm`}>
                <Slider value={cfg.warmThreshold} min={10} max={cfg.hotThreshold - 5} step={5}
                  color="#f59e0b" onChange={v => set("warmThreshold", v)} />
              </Field>
            </Section>
          )}

          {/* ── EXPORT ── */}
          {active === "export" && (
            <Section icon="ti-download" color="#a855f7" bg="rgba(168,85,247,0.13)"
              title="Export Defaults" sub="Default format when exporting leads">
              <Field label="Default Format" hint="Used when you click export on Leads page">
                <div className="st-radio-group">
                  {["csv","excel","json"].map(f => (
                    <label key={f} className={"st-radio" + (cfg.exportFormat === f ? " st-radio--active" : "")}>
                      <input type="radio" name="exportFormat" value={f}
                        checked={cfg.exportFormat === f} onChange={() => set("exportFormat", f)} />
                      <i className={`ti ${f === "csv" ? "ti-file-type-csv" : f === "excel" ? "ti-file-type-xls" : "ti-file-type-json"}`} />
                      <span>{f.toUpperCase()}</span>
                    </label>
                  ))}
                </div>
              </Field>
              <Field label="Auto-export after scrape" hint="Automatically download file when scraping completes">
                <Toggle checked={cfg.autoExport} onChange={v => set("autoExport", v)} />
              </Field>
            </Section>
          )}

          {/* ── DANGER ZONE ── */}
          {active === "danger" && (
            <Section icon="ti-alert-triangle" color="#f43f5e" bg="rgba(244,63,94,0.13)"
              title="Danger Zone" sub="These actions reset saved preferences — cannot be undone">
              {!danger ? (
                <div className="st-danger-prompt">
                  <i className="ti ti-alert-octagon" />
                  <p>Resetting will clear all your saved settings and restore factory defaults.</p>
                  <button className="st-danger-btn" onClick={() => setDanger(true)}>
                    <i className="ti ti-trash" /> Reset all settings
                  </button>
                </div>
              ) : (
                <div className="st-danger-confirm">
                  <p className="st-danger-q">Are you sure? This will wipe all your saved preferences.</p>
                  <div className="st-danger-actions">
                    <button className="st-btn-ghost" onClick={() => setDanger(false)}>Cancel</button>
                    <button className="st-danger-btn" onClick={handleReset}>
                      <i className="ti ti-trash" /> Yes, reset everything
                    </button>
                  </div>
                </div>
              )}
            </Section>
          )}

          {active !== "danger" && (
            <div className="st-bottom-save">
              <button className="st-save-btn" onClick={handleSave}>
                <i className="ti ti-device-floppy" /> Save changes
              </button>
            </div>
          )}
        </div>
      </div>

      <Toast show={toast} />
    </div>
  );
}