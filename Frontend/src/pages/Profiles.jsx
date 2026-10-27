import { useState, useEffect, useCallback, useRef } from "react";
import "./Profiles.css";
import { readError } from "../utils/apiErrors";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── Rule options (kept in step with Backend/profile_schema.py) ────────────────
const KINDS = [
  { value: "field",   label: "Field value",     hint: "Compare a stored value, e.g. review_count ≥ 50" },
  { value: "keyword", label: "Website keywords", hint: "Words found on the lead's website" },
  { value: "regex",   label: "Website pattern",  hint: "A pattern (regex) found on the website" },
  { value: "ai",      label: "AI question",      hint: "A yes/no question the AI answers about the lead" },
  { value: "builtin", label: "Built-in check",   hint: "A detector the system already runs" },
  { value: "intent",  label: "Lead behaviour",   hint: "Something the lead did after outreach" },
];
const FIELD_OPS = [">=", "<=", ">", "<", "==", "!=", "exists", "in", "contains"];
const BUILTINS = [
  { value: "dropship_mentioned",   label: "Dropshipping mentioned" },
  { value: "processing_fast",      label: "Processing time 1–2 days" },
  { value: "delivery_fast",        label: "Delivery time 4–5 days" },
  { value: "sells_on_marketplace", label: "Sells on Amazon / Etsy / Shopify" },
];
const EVENTS = [
  { value: "email_opened",   label: "Opened an email" },
  { value: "replied",        label: "Replied" },
  { value: "call_connected", label: "Call connected" },
  { value: "meeting_booked", label: "Meeting booked" },
];
const CHANNELS = [
  { value: "email",    label: "Email",    icon: "ti-mail" },
  { value: "whatsapp", label: "WhatsApp", icon: "ti-brand-whatsapp" },
  { value: "call",     label: "Call",     icon: "ti-phone" },
];
const TIER_OPTIONS = [
  { value: "hot",  label: "Hot only" },
  { value: "warm", label: "Hot and Warm" },
  { value: "cold", label: "Everyone" },
];
const DEFAULT_STEPS = [
  { key: "email_1",    label: "First email",      channel: "email",    delay_days: 0, min_tier: "cold" },
  { key: "email_2",    label: "Follow-up email",  channel: "email",    delay_days: 3, min_tier: "cold" },
  { key: "whatsapp_1", label: "WhatsApp message", channel: "whatsapp", delay_days: 1, min_tier: "warm" },
  { key: "email_3",    label: "New-angle email",  channel: "email",    delay_days: 3, min_tier: "cold" },
  { key: "call_1",     label: "Call",             channel: "call",     delay_days: 2, min_tier: "warm" },
  { key: "email_4",    label: "Closing note",     channel: "email",    delay_days: 5, min_tier: "cold" },
];
const FIELD_SUGGESTIONS = ["rating", "review_count", "website", "email", "phone", "city", "country", "niche"];
const REQUIRES = ["website", "email", "phone"];

// ─── helpers ──────────────────────────────────────────────────────────────────
let uidCounter = 0;
const uid = () => `r${++uidCounter}`;

function parseScalar(s) {
  const t = String(s).trim();
  if (t === "true") return true;
  if (t === "false") return false;
  if (t !== "" && !Number.isNaN(Number(t))) return Number(t);
  return t;
}
function parseValue(op, s) {
  if (op === "in") return String(s).split(",").map(parseScalar).filter(v => v !== "");
  return parseScalar(s);
}
function valueToText(v) {
  if (Array.isArray(v)) return v.join(", ");
  if (v === undefined || v === null) return "";
  return String(v);
}
const splitWords = (s) => String(s).split(/[\n,]/).map(w => w.trim()).filter(Boolean);

function slugKey(label) {
  let k = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!k) k = "rule";
  if (/^[0-9]/.test(k)) k = `r_${k}`;
  return k.slice(0, 60);
}

function emptyRule() {
  return { uid: uid(), key: "", label: "", kind: "keyword", points: 10, group: "base",
           requires: [], p: { any: "", none: "", field: "", op: ">=", value: "", pattern: "",
                              question: "", name: BUILTINS[0].value, event: EVENTS[0].value } };
}

function ruleFromApi(r) {
  const base = emptyRule();
  const p = r.params ?? {};
  return {
    ...base, key: r.key, label: r.label, kind: r.kind, points: r.points,
    group: r.group, requires: r.requires ?? [],
    p: {
      ...base.p,
      any: (p.any ?? []).join("\n"), none: (p.none ?? []).join("\n"),
      field: p.field ?? "", op: p.op ?? ">=", value: valueToText(p.value),
      pattern: p.pattern ?? "", question: p.question ?? "",
      name: p.name ?? base.p.name, event: p.event ?? base.p.event,
    },
  };
}

const stepToForm = (st) => ({ uid: uid(), ...st });

function blankForm() {
  return {
    id: null, name: "", description: "", target_type: "business",
    query_template: "{niche} {city}", default_niche: "",
    summary: "", tone: "friendly, professional", language: "en", booking_link: "", faq: "", knowledge: "",
    hot: "", warm: "", rules: [],
    steps: DEFAULT_STEPS.map(stepToForm),
  };
}

function formFromProfile(p) {
  const c = p.config;
  return {
    id: p.id, name: p.name, description: p.description ?? "", target_type: p.target_type,
    query_template: c.search.query_template, default_niche: c.search.default_niche ?? "",
    summary: c.offer.summary, tone: c.offer.tone, language: c.offer.language,
    booking_link: c.offer.booking_link ?? "", faq: c.offer.faq ?? "", knowledge: c.offer.knowledge ?? "",
    hot: c.scoring.hot_threshold ?? "", warm: c.scoring.warm_threshold ?? "",
    rules: c.scoring.rules.map(ruleFromApi),
    steps: c.sequence.steps.map(stepToForm),
  };
}

function ruleToApi(r, key) {
  let params;
  switch (r.kind) {
    case "field":
      params = { field: r.p.field.trim(), op: r.p.op };
      if (r.p.op !== "exists") params.value = parseValue(r.p.op, r.p.value);
      break;
    case "keyword": {
      params = { source: "website_text", any: splitWords(r.p.any) };
      const none = splitWords(r.p.none);
      if (none.length) params.none = none;
      break;
    }
    case "regex":   params = { pattern: r.p.pattern }; break;
    case "ai":      params = { question: r.p.question.trim() }; break;
    case "builtin": params = { name: r.p.name }; break;
    default:        params = { event: r.p.event };
  }
  return { key, label: r.label.trim(), kind: r.kind, points: Number(r.points) || 0,
           group: r.group, requires: r.requires, params };
}

function payloadFromForm(f) {
  const used = new Set(f.rules.map(r => r.key).filter(Boolean));
  const rules = f.rules.map(r => {
    let key = r.key;
    if (!key) {
      const base = slugKey(r.label);
      key = base;
      for (let n = 2; used.has(key); n++) key = `${base}_${n}`;
      used.add(key);
    }
    return ruleToApi(r, key);
  });
  const usedStepKeys = new Set(f.steps.map(st => st.key).filter(Boolean));
  const steps = f.steps.map((st, i) => {
    let key = st.key;
    if (!key) {
      const base = slugKey(st.label || `${st.channel}_${i + 1}`);
      key = base;
      for (let n = 2; usedStepKeys.has(key); n++) key = `${base}_${n}`;
      usedStepKeys.add(key);
    }
    return { key, label: st.label.trim(), channel: st.channel,
             delay_days: Number(st.delay_days) || 0, min_tier: st.min_tier };
  });
  const num = (v) => (v === "" || v === null ? null : Number(v));
  return {
    name: f.name.trim(),
    description: f.description.trim() || null,
    target_type: f.target_type,
    config: {
      search: { query_template: f.query_template.trim(), default_niche: f.default_niche.trim() || null },
      offer: {
        summary: f.summary.trim(), tone: f.tone.trim(), language: f.language.trim() || "en",
        booking_link: f.booking_link.trim(), faq: f.faq.trim(), knowledge: f.knowledge.trim(),
      },
      scoring: { hot_threshold: num(f.hot), warm_threshold: num(f.warm), rules },
      sequence: { steps },
    },
  };
}

// ─── Stat card (same markup as the other pages) ───────────────────────────────
function StatCard({ icon, color, bg, label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-top">
        <div className="sc-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
      </div>
      <div className="sc-value">
        {loading ? <span className="skeleton" style={{ width: 50, display: "inline-block" }} /> : (value ?? 0).toLocaleString()}
      </div>
      <div className="sc-label">{label}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

// ─── One scoring rule in the editor ───────────────────────────────────────────
function RuleEditor({ rule, onChange, onRemove }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  const setP = (patch) => onChange({ ...rule, p: { ...rule.p, ...patch } });
  const toggleReq = (name) =>
    set({ requires: rule.requires.includes(name) ? rule.requires.filter(x => x !== name) : [...rule.requires, name] });
  const kind = KINDS.find(k => k.value === rule.kind);

  return (
    <div className="pf-rule">
      <div className="pf-rule-top">
        <input className="pf-input pf-rule-label" placeholder="What are we checking? e.g. No online booking"
               value={rule.label} onChange={e => set({ label: e.target.value })} />
        <input className="pf-input pf-points" type="number" min="-100" max="100" title="Points (negative = penalty)"
               value={rule.points} onChange={e => set({ points: e.target.value })} />
        <select className="filter-select" value={rule.group} onChange={e => set({ group: e.target.value })}
                title="Base counts toward the 100-point score, Bonus is extra">
          <option value="base">Base</option>
          <option value="bonus">Bonus</option>
        </select>
        <button type="button" className="pf-icon-btn pf-icon-btn--danger" onClick={onRemove} title="Remove rule">
          <i className="ti ti-trash" />
        </button>
      </div>

      <div className="pf-rule-mid">
        <select className="filter-select" value={rule.kind} onChange={e => set({ kind: e.target.value })}>
          {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>

        {rule.kind === "field" && (
          <>
            <input className="pf-input" list="pf-fields" placeholder="field, e.g. review_count"
                   value={rule.p.field} onChange={e => setP({ field: e.target.value })} />
            <select className="filter-select" value={rule.p.op} onChange={e => setP({ op: e.target.value })}>
              {FIELD_OPS.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            {rule.p.op !== "exists" && (
              <input className="pf-input" placeholder={rule.p.op === "in" ? "a, b, c" : "value"}
                     value={rule.p.value} onChange={e => setP({ value: e.target.value })} />
            )}
          </>
        )}
        {rule.kind === "regex" && (
          <input className="pf-input pf-mono" placeholder="pattern, e.g. \(\d{3}\) \d{3}-\d{4}"
                 value={rule.p.pattern} onChange={e => setP({ pattern: e.target.value })} />
        )}
        {rule.kind === "ai" && (
          <input className="pf-input" placeholder="Does the website have a live chat or chatbot?"
                 value={rule.p.question} onChange={e => setP({ question: e.target.value })} />
        )}
        {rule.kind === "builtin" && (
          <select className="filter-select" value={rule.p.name} onChange={e => setP({ name: e.target.value })}>
            {BUILTINS.map(b => <option key={b.value} value={b.value}>{b.label}</option>)}
          </select>
        )}
        {rule.kind === "intent" && (
          <select className="filter-select" value={rule.p.event} onChange={e => setP({ event: e.target.value })}>
            {EVENTS.map(b => <option key={b.value} value={b.value}>{b.label}</option>)}
          </select>
        )}
      </div>

      {rule.kind === "keyword" && (
        <div className="pf-rule-words">
          <div className="form-group">
            <label>Match if the site contains any of (one per line)</label>
            <textarea className="pf-textarea" rows={3} placeholder={"24/7\nemergency service"}
                      value={rule.p.any} onChange={e => setP({ any: e.target.value })} />
          </div>
          <div className="form-group">
            <label>…but not if it contains (optional)</label>
            <textarea className="pf-textarea" rows={3} placeholder={"book online\nschedule online"}
                      value={rule.p.none} onChange={e => setP({ none: e.target.value })} />
          </div>
        </div>
      )}

      <div className="pf-rule-foot">
        <span className="pf-hint">{kind?.hint}</span>
        <span className="pf-req">
          Needs:
          {REQUIRES.map(r => (
            <label key={r} className="pf-check">
              <input type="checkbox" checked={rule.requires.includes(r)} onChange={() => toggleReq(r)} /> {r}
            </label>
          ))}
        </span>
      </div>
    </div>
  );
}

// ─── One follow-up step in the editor ─────────────────────────────────────────
function StepEditor({ step, index, count, onChange, onMove, onRemove }) {
  const set = (patch) => onChange({ ...step, ...patch });
  return (
    <div className="pf-step">
      <span className="pf-step-no">{index + 1}</span>
      <select className="filter-select" value={step.channel} onChange={e => set({ channel: e.target.value })}>
        {CHANNELS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      <input className="pf-input pf-step-label" placeholder="Name, e.g. Follow-up email"
             value={step.label} onChange={e => set({ label: e.target.value })} />
      <label className="pf-step-wait" title="Days to wait after the previous step was done">
        {index === 0 ? "Start" : "Wait"}
        <input className="pf-input pf-step-days" type="number" min="0" max="90" step="0.5"
               value={step.delay_days} onChange={e => set({ delay_days: e.target.value })}
               disabled={index === 0} />
        days
      </label>
      <select className="filter-select" value={step.min_tier} onChange={e => set({ min_tier: e.target.value })}
              title="Which leads get this step">
        {TIER_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>
      <div className="pf-step-btns">
        <button type="button" className="pf-icon-btn" disabled={index === 0} onClick={() => onMove(-1)} title="Move up">
          <i className="ti ti-arrow-up" />
        </button>
        <button type="button" className="pf-icon-btn" disabled={index === count - 1} onClick={() => onMove(1)} title="Move down">
          <i className="ti ti-arrow-down" />
        </button>
        <button type="button" className="pf-icon-btn pf-icon-btn--danger" onClick={onRemove} title="Remove step">
          <i className="ti ti-trash" />
        </button>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function Profiles() {
  const [profiles, setProfiles] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [show,     setShow]     = useState("active");     // active | archived | all
  const [notice,   setNotice]   = useState(null);         // { type: "ok" | "err", text }

  const [form,     setForm]     = useState(null);         // null = modal closed
  const [saving,   setSaving]   = useState(false);
  const [formErr,  setFormErr]  = useState("");
  const fileRef = useRef(null);

  const flash = (type, text) => {
    setNotice({ type, text });
    setTimeout(() => setNotice(n => (n && n.text === text ? null : n)), 5000);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/profiles?include_archived=true`);
      if (!res.ok) throw new Error(await readError(res));
      setProfiles(await res.json());
    } catch (e) {
      flash("err", `Could not load profiles: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const active   = profiles.filter(p => p.is_active);
  const archived = profiles.filter(p => !p.is_active);
  const visible  = show === "active" ? active : show === "archived" ? archived : profiles;
  const leadsCovered = profiles.reduce((n, p) => n + (p.lead_count ?? 0), 0);

  // ── actions ──
  async function post(path, okText) {
    try {
      const res = await fetch(`${API}${path}`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      flash("ok", okText);
      load();
    } catch (e) { flash("err", e.message); }
  }

  async function handleExport(p) {
    try {
      const res = await fetch(`${API}/profiles/${p.id}/export`);
      if (!res.ok) throw new Error(await readError(res));
      const blob = new Blob([JSON.stringify(await res.json(), null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `${p.slug}.json`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { flash("err", e.message); }
  }

  async function handleImport(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const res = await fetch(`${API}/profiles/import`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const created = await res.json();
      flash("ok", `Imported "${created.name}"`);
      load();
    } catch (err) {
      flash("err", err instanceof SyntaxError ? "That file is not valid JSON." : err.message);
    }
  }

  async function handleSave() {
    setFormErr("");
    if (form.name.trim().length < 2) { setFormErr("Give the profile a name."); return; }
    if (form.rules.some(r => !r.label.trim())) { setFormErr("Every rule needs a description."); return; }
    setSaving(true);
    try {
      const res = await fetch(`${API}/profiles${form.id ? `/${form.id}` : ""}`, {
        method: form.id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payloadFromForm(form)),
      });
      if (!res.ok) { setFormErr(await readError(res)); return; }
      const saved = await res.json();
      setForm(null);
      flash("ok", form.id ? `Saved "${saved.name}" (version ${saved.version})` : `Created "${saved.name}"`);
      load();
    } catch {
      setFormErr("Network error — check your connection.");
    } finally {
      setSaving(false);
    }
  }

  const setF = (patch) => setForm(f => ({ ...f, ...patch }));
  const updateStep = (i, step) => setForm(f => ({ ...f, steps: f.steps.map((x, j) => (j === i ? step : x)) }));
  const moveStep = (i, dir) => setForm(f => {
    const steps = [...f.steps];
    [steps[i], steps[i + dir]] = [steps[i + dir], steps[i]];
    return { ...f, steps };
  });
  const updateRule = (i, rule) => setForm(f => ({ ...f, rules: f.rules.map((r, j) => (j === i ? rule : r)) }));

  return (
    <div className="pf-page">

      <div className="stat-grid pf-stats">
        <StatCard icon="ti-briefcase" color="#7c3aed" bg="rgba(124,58,237,0.08)"
                  label="Active profiles" value={active.length} sub="Available when scraping" loading={loading} />
        <StatCard icon="ti-archive" color="#f59e0b" bg="rgba(245,158,11,0.08)"
                  label="Archived" value={archived.length} sub="Hidden from pickers" loading={loading} />
        <StatCard icon="ti-users" color="#4ade80" bg="rgba(74,222,128,0.08)"
                  label="Leads scored by profiles" value={leadsCovered} sub="Across all profiles" loading={loading} />
      </div>

      {notice && (
        <div className={`pf-notice pf-notice--${notice.type}`}>
          <i className={`ti ${notice.type === "ok" ? "ti-check" : "ti-alert-circle"}`} />
          {notice.text}
        </div>
      )}

      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">Industry profiles</div>
            <div className="panel-sub">
              A profile holds everything that changes between industries: how leads are searched for, how they are scored, and what you offer
            </div>
          </div>
          <div className="table-actions">
            <select className="filter-select" value={show} onChange={e => setShow(e.target.value)}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </select>
            <button className="btn-ghost" onClick={() => fileRef.current?.click()}>
              <i className="ti ti-upload" /> <span>Import</span>
            </button>
            <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={handleImport} />
            <button className="btn-primary" onClick={() => { setForm(blankForm()); setFormErr(""); }}>
              <i className="ti ti-plus" /> <span>New profile</span>
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Profile</th>
                <th>Search wording</th>
                <th>Rules</th>
                <th>Leads</th>
                <th>Version</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 3 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      {Array.from({ length: 7 }).map((_, j) => <td key={j}><div className="skeleton" /></td>)}
                    </tr>
                  ))
                : visible.length === 0
                ? (
                    <tr>
                      <td colSpan={7} className="empty-state">
                        <i className="ti ti-briefcase" />
                        <p>No {show === "all" ? "" : show} profiles</p>
                        <span>Create one to target a new industry</span>
                      </td>
                    </tr>
                  )
                : visible.map(p => (
                    <tr key={p.id}>
                      <td>
                        <div className="pf-name">{p.name}</div>
                        <div className="pf-desc">
                          {p.description || (p.target_type === "person" ? "Targets individual people" : "Targets businesses")}
                        </div>
                      </td>
                      <td><code className="pf-code">{p.config.search.query_template}</code></td>
                      <td>{p.config.scoring.rules.length}</td>
                      <td>{p.lead_count.toLocaleString()}</td>
                      <td className="date-cell">v{p.version}</td>
                      <td>
                        <span className={`pf-badge ${p.is_active ? "pf-badge--on" : "pf-badge--off"}`}>
                          {p.is_active ? "Active" : "Archived"}
                        </span>
                      </td>
                      <td>
                        <div className="pf-actions">
                          <button className="pf-icon-btn" title="Edit"
                                  onClick={() => { setForm(formFromProfile(p)); setFormErr(""); }}>
                            <i className="ti ti-pencil" />
                          </button>
                          <button className="pf-icon-btn" title="Duplicate"
                                  onClick={() => post(`/profiles/${p.id}/duplicate`, `Copied "${p.name}"`)}>
                            <i className="ti ti-copy" />
                          </button>
                          <button className="pf-icon-btn" title="Export" onClick={() => handleExport(p)}>
                            <i className="ti ti-download" />
                          </button>
                          {p.is_active
                            ? <button className="pf-icon-btn pf-icon-btn--danger" title="Archive"
                                      onClick={() => post(`/profiles/${p.id}/archive`, `Archived "${p.name}"`)}>
                                <i className="ti ti-archive" />
                              </button>
                            : <button className="pf-icon-btn" title="Restore"
                                      onClick={() => post(`/profiles/${p.id}/restore`, `Restored "${p.name}"`)}>
                                <i className="ti ti-arrow-back-up" />
                              </button>}
                        </div>
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </div>

      <datalist id="pf-fields">
        {FIELD_SUGGESTIONS.map(f => <option key={f} value={f} />)}
      </datalist>

      {/* ── edit / create modal ── */}
      {form && (
        <div className="modal-overlay" onClick={() => !saving && setForm(null)}>
          <div className="modal pf-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">{form.id ? "Edit profile" : "New profile"}</div>
                <div className="modal-sub">
                  {form.id ? "Saving creates a new version. Leads already scored keep their old score until re-audited."
                           : "Describe who you are looking for and how to recognise a good lead"}
                </div>
              </div>
              <button className="modal-close" onClick={() => setForm(null)}><i className="ti ti-x" /></button>
            </div>

            <div className="modal-body pf-body">

              <div className="pf-section">Basics</div>
              <div className="form-row">
                <div className="form-group">
                  <label>Name</label>
                  <input type="text" placeholder="e.g. HVAC contractors (US)" value={form.name}
                         onChange={e => setF({ name: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Looking for</label>
                  <select className="filter-select pf-select" value={form.target_type}
                          onChange={e => setF({ target_type: e.target.value })}>
                    <option value="business">Businesses</option>
                    <option value="person">Individual people</option>
                  </select>
                </div>
              </div>
              <div className="form-group">
                <label>Description</label>
                <input type="text" placeholder="Optional note for your team" value={form.description}
                       onChange={e => setF({ description: e.target.value })} />
              </div>

              <div className="pf-section">Search</div>
              <div className="form-row">
                <div className="form-group">
                  <label>Search wording</label>
                  <input type="text" className="pf-mono" value={form.query_template}
                         onChange={e => setF({ query_template: e.target.value })} />
                  <span className="pf-hint">Use {"{niche}"} and {"{city}"}. Example result: “{form.query_template.replace("{niche}", "plumbers").replace("{city}", "Austin TX")}”</span>
                </div>
                <div className="form-group">
                  <label>Default niche</label>
                  <input type="text" placeholder="Optional, e.g. HVAC" value={form.default_niche}
                         onChange={e => setF({ default_niche: e.target.value })} />
                </div>
              </div>

              <div className="pf-section">Offer</div>
              <div className="form-group">
                <label>What you offer</label>
                <textarea className="pf-textarea" rows={3}
                          placeholder="A few sentences the AI uses when writing messages"
                          value={form.summary} onChange={e => setF({ summary: e.target.value })} />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Tone</label>
                  <input type="text" value={form.tone} onChange={e => setF({ tone: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Language</label>
                  <input type="text" placeholder="en" value={form.language}
                         onChange={e => setF({ language: e.target.value })} />
                </div>
              </div>

              <div className="form-group">
                <label>Booking link</label>
                <input type="text" placeholder="Where an interested lead can book a call (optional)" value={form.booking_link}
                       onChange={e => setF({ booking_link: e.target.value })} />
              </div>
              <div className="form-group">
                <label>Questions and answers</label>
                <textarea className="pf-textarea" rows={4}
                          placeholder={"Q: How much does it cost?\nA: ...\n\nWhen a lead asks something, the AI may use only this and the text above."}
                          value={form.faq} onChange={e => setF({ faq: e.target.value })} />
              </div>
              <div className="form-group">
                <label>Knowledge base</label>
                <textarea className="pf-textarea" rows={10} maxLength={14000}
                          placeholder={"Everything the AI may use to answer questions about what you offer this industry: services, benefits, how it works, what you do not do.\n\nNever put prices here."}
                          value={form.knowledge} onChange={e => setF({ knowledge: e.target.value })} />
                <span className="pf-hint">Used when answering a lead's reply, and in part when writing first emails. {form.knowledge.length.toLocaleString()} of 14,000 characters.</span>
              </div>

              <div className="pf-section">Scoring</div>
              <div className="form-row">
                <div className="form-group">
                  <label>Hot from (score)</label>
                  <input type="number" min="0" placeholder="Use the Settings page value" value={form.hot}
                         onChange={e => setF({ hot: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Warm from (score)</label>
                  <input type="number" min="0" placeholder="Use the Settings page value" value={form.warm}
                         onChange={e => setF({ warm: e.target.value })} />
                </div>
              </div>

              <div className="pf-rules-head">
                <span className="pf-hint">
                  Each rule adds its points to a lead's score when it matches. Base rules make up the 100-point score;
                  Bonus rules add on top. Use a negative number for a penalty.
                </span>
              </div>

              {form.rules.length === 0 && (
                <div className="pf-empty-rules">No rules yet. Without rules every lead scores 0.</div>
              )}
              {form.rules.map((r, i) => (
                <RuleEditor key={r.uid} rule={r}
                            onChange={rule => updateRule(i, rule)}
                            onRemove={() => setForm(f => ({ ...f, rules: f.rules.filter((_, j) => j !== i) }))} />
              ))}
              <button type="button" className="btn-ghost pf-add-rule"
                      onClick={() => setForm(f => ({ ...f, rules: [...f.rules, emptyRule()] }))}>
                <i className="ti ti-plus" /> Add rule
              </button>

              <div className="pf-section">Follow-up sequence</div>
              <span className="pf-hint">
                Steps run in this order for every lead. Each wait is counted from when the previous step was done,
                and the next step is moved into the lead's own working hours. Steps a lead cannot receive
                (no email, wrong tier) are skipped.
              </span>
              {form.steps.length === 0 && (
                <div className="pf-empty-rules">No steps. Leads in the queue will have nothing to do.</div>
              )}
              {form.steps.map((st, i) => (
                <StepEditor key={st.uid} step={st} index={i} count={form.steps.length}
                            onChange={step => updateStep(i, step)}
                            onMove={dir => moveStep(i, dir)}
                            onRemove={() => setForm(f => ({ ...f, steps: f.steps.filter((_, j) => j !== i) }))} />
              ))}
              <button type="button" className="btn-ghost pf-add-rule"
                      onClick={() => setForm(f => ({ ...f, steps: [...f.steps, stepToForm(
                        { key: "", label: "", channel: "email", delay_days: 3, min_tier: "warm" })] }))}>
                <i className="ti ti-plus" /> Add step
              </button>

              {formErr && (
                <div className="pf-notice pf-notice--err">
                  <i className="ti ti-alert-circle" />{formErr}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setForm(null)} disabled={saving}>Cancel</button>
              <button className="btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? <><i className="ti ti-loader-2 spin" /> Saving...</>
                        : <><i className="ti ti-device-floppy" /> {form.id ? "Save changes" : "Create profile"}</>}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
