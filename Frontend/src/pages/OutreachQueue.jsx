import { useState, useEffect, useCallback } from "react";
import "./OutreachQueue.css";
import { readError } from "../utils/apiErrors";
import MessageApprovals from "../components/MessageApprovals";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const CHANNEL_META = {
  email:    { label: "Email",    icon: "ti-mail" },
  whatsapp: { label: "WhatsApp", icon: "ti-brand-whatsapp" },
  call:     { label: "Call",     icon: "ti-phone" },
};
const MODES = [
  { value: "manual",   label: "Manual",   hint: "You do it, then press Done" },
  { value: "assisted", label: "Assisted", hint: "AI prepares it, you approve" },
  { value: "auto",     label: "Auto",     hint: "Sent by itself within the limits" },
];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const REPLY_LABELS = {
  interested: { text: "Interested", cls: "oq-reply--good" },
  question:   { text: "Has a question", cls: "oq-reply--good" },
  not_now:    { text: "Not now", cls: "oq-reply--wait" },
  stop:       { text: "Asked to stop", cls: "oq-reply--bad" },
  other:      { text: "Other", cls: "" },
};
const shortDate = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const LIMIT = 12;

// ─── helpers ──────────────────────────────────────────────────────────────────
function tierClass(tier) {
  return { hot: "tier-hot", warm: "tier-warm", cold: "tier-cold" }[tier] ?? "tier-unscored";
}
const tierLabel = (tier) => ({ hot: "Hot", warm: "Warm", cold: "Cold" }[tier] ?? "—");

// "due now", "in 3 days", "2 h overdue"…
function whenText(iso, dueNow) {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(diff);
  const mins = Math.round(abs / 60000);
  const text = mins < 60 ? `${mins} min`
    : mins < 60 * 36 ? `${Math.round(mins / 60)} h`
    : `${Math.round(mins / 1440)} days`;
  if (dueNow || diff <= 0) return abs < 60000 ? "Due now" : `Due ${text} ago`;
  return `In ${text}`;
}

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

// ─── Sending rules (shared by every profile) ──────────────────────────────────
function RulesModal({ onClose, onSaved }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/outreach-queue/schedule-settings`)
      .then(r => r.json()).then(setForm)
      .catch(() => setError("Could not load the sending rules."));
  }, []);

  const set = (patch) => setForm(f => ({ ...f, ...patch }));
  const setChannel = (ch, patch) => setForm(f => ({ ...f, [ch]: { ...f[ch], ...patch } }));
  const toggleDay = (d) => set({
    work_days: form.work_days.includes(d) ? form.work_days.filter(x => x !== d) : [...form.work_days, d].sort(),
  });

  async function save() {
    setSaving(true);
    setError("");
    try {
      const body = { ...form, revisit_days: Number(form.revisit_days) || 45 };
      for (const ch of Object.keys(CHANNEL_META)) {
        body[ch] = {
          mode: form[ch].mode,
          min_gap_seconds: Number(form[ch].min_gap_seconds) || 0,
          max_gap_seconds: Number(form[ch].max_gap_seconds) || 0,
          daily_cap: Number(form[ch].daily_cap) || 0,
        };
      }
      const res = await fetch(`${API}/outreach-queue/schedule-settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) { setError(await readError(res)); return; }
      onSaved();
    } catch {
      setError("Network error — check your connection.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={() => !saving && onClose()}>
      <div className="modal oq-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">Sending rules</div>
            <div className="modal-sub">Shared by every profile. Working hours are the lead's own local time.</div>
          </div>
          <button className="modal-close" onClick={onClose}><i className="ti ti-x" /></button>
        </div>

        <div className="modal-body oq-body">
          {!form ? <div className="oq-hint">{error || "Loading…"}</div> : (
            <>
              <div className="form-row">
                <div className="form-group">
                  <label>Working hours from</label>
                  <input type="time" value={form.work_start} onChange={e => set({ work_start: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Working hours until</label>
                  <input type="time" value={form.work_end} onChange={e => set({ work_end: e.target.value })} />
                </div>
              </div>

              <div className="form-group">
                <label>Working days</label>
                <div className="oq-days">
                  {DAYS.map((d, i) => (
                    <button key={d} type="button"
                            className={`oq-day ${form.work_days.includes(i) ? "oq-day--on" : ""}`}
                            onClick={() => toggleDay(i)}>{d}</button>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label>Time zone when a lead's own is unknown</label>
                <input type="text" placeholder="America/New_York" value={form.fallback_timezone}
                       onChange={e => set({ fallback_timezone: e.target.value })} />
                <span className="oq-hint">Daily limits also reset at midnight in this zone.</span>
              </div>

              <div className="oq-section">Replies</div>
              <div className="form-row">
                <div className="form-group">
                  <label>Contact "not now" leads again after (days)</label>
                  <input type="number" min="1" max="365" value={form.revisit_days}
                         onChange={e => set({ revisit_days: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>AI answers</label>
                  <label className="oq-check">
                    <input type="checkbox" checked={form.auto_reply} onChange={e => set({ auto_reply: e.target.checked })} />
                    Send without approval on Auto channels
                  </label>
                </div>
              </div>

              <div className="oq-section">Channels</div>
              {Object.entries(CHANNEL_META).map(([ch, meta]) => (
                <div className="oq-channel" key={ch}>
                  <div className="oq-channel-name"><i className={`ti ${meta.icon}`} /> {meta.label}</div>
                  <div className="oq-channel-grid">
                    <div className="form-group">
                      <label>Mode</label>
                      <select className="filter-select oq-select" value={form[ch].mode}
                              onChange={e => setChannel(ch, { mode: e.target.value })}>
                        {MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                      </select>
                    </div>
                    <div className="form-group">
                      <label>Gap between sends (sec)</label>
                      <div className="oq-gap">
                        <input type="number" min="0" value={form[ch].min_gap_seconds}
                               onChange={e => setChannel(ch, { min_gap_seconds: e.target.value })} />
                        <span>to</span>
                        <input type="number" min="0" value={form[ch].max_gap_seconds}
                               onChange={e => setChannel(ch, { max_gap_seconds: e.target.value })} />
                      </div>
                    </div>
                    <div className="form-group">
                      <label>Daily limit</label>
                      <input type="number" min="0" value={form[ch].daily_cap}
                             onChange={e => setChannel(ch, { daily_cap: e.target.value })} />
                    </div>
                  </div>
                  <span className="oq-hint">{MODES.find(m => m.value === form[ch].mode)?.hint}. The gap is random within the range.</span>
                </div>
              ))}

              {error && <div className="oq-notice oq-notice--err"><i className="ti ti-alert-circle" />{error}</div>}
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn-primary" onClick={save} disabled={saving || !form}>
            {saving ? <><i className="ti ti-loader-2 spin" /> Saving...</> : <><i className="ti ti-device-floppy" /> Save rules</>}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function OutreachQueue() {
  const [data,     setData]     = useState({ total: 0, counts: {}, due_now: 0, items: [] });
  const [plan,     setPlan]     = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [notice,   setNotice]   = useState(null);

  const [status,   setStatus]   = useState("queued");
  const [tier,     setTier]     = useState("");
  const [profile,  setProfile]  = useState("");
  const [dueOnly,  setDueOnly]  = useState(false);
  const [search,   setSearch]   = useState("");
  const [page,     setPage]     = useState(1);
  const [rulesOpen, setRulesOpen] = useState(false);

  const flash = (type, text) => {
    setNotice({ type, text });
    setTimeout(() => setNotice(n => (n && n.text === text ? null : n)), 5000);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ status, limit: LIMIT, offset: (page - 1) * LIMIT });
      if (tier)    params.set("tier", tier);
      if (profile) params.set("profile_id", profile);
      if (search)  params.set("search", search);
      if (dueOnly) params.set("due", "true");
      const [qRes, pRes] = await Promise.all([
        fetch(`${API}/outreach-queue?${params}`),
        fetch(`${API}/outreach-queue/plan`),
      ]);
      if (!qRes.ok) throw new Error(await readError(qRes));
      setData(await qRes.json());
      if (pRes.ok) setPlan(await pRes.json());
    } catch (e) {
      flash("err", `Could not load the queue: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [status, tier, profile, search, dueOnly, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    fetch(`${API}/profiles`).then(r => r.json()).then(setProfiles).catch(() => {});
  }, []);

  // reset to page 1 whenever a filter changes
  useEffect(() => { setPage(1); }, [status, tier, profile, search, dueOnly]);

  async function act(leadId, action, okText) {
    try {
      const res = await fetch(`${API}/outreach-queue/${leadId}/${action}`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      if (okText) flash("ok", okText);
      load();
    } catch (e) { flash("err", e.message); }
  }

  async function sync() {
    try {
      const res = await fetch(`${API}/outreach-queue/sync`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      const { added } = await res.json();
      flash("ok", added === 0 ? "No new leads to add" : `Added ${added} lead${added === 1 ? "" : "s"} to the queue`);
      load();
    } catch (e) { flash("err", e.message); }
  }

  const sentToday = plan ? Object.values(plan).reduce((n, c) => n + (c.daily_cap - c.cap_left), 0) : 0;
  const totalPages = Math.max(1, Math.ceil(data.total / LIMIT));
  const { items } = data;

  return (
    <div className="oq-page">

      <div className="stat-grid">
        <StatCard icon="ti-list-check" color="#e8622a" bg="rgba(232,98,42,0.08)"
                  label="In the queue" value={data.counts.queued} sub="Hot and Warm leads waiting" loading={loading} />
        <StatCard icon="ti-bell-ringing" color="#4ade80" bg="rgba(74,222,128,0.08)"
                  label="Due now" value={data.due_now} sub="Next step is ready" loading={loading} />
        <StatCard icon="ti-player-pause" color="#f59e0b" bg="rgba(245,158,11,0.08)"
                  label="Paused" value={data.counts.paused} sub="On hold" loading={loading} />
        <StatCard icon="ti-send" color="#60a5fa" bg="rgba(96,165,250,0.08)"
                  label="Done today" value={sentToday} sub="Emails, WhatsApp and calls" loading={loading} />
      </div>

      {plan && (
        <div className="oq-channels">
          {Object.entries(CHANNEL_META).map(([ch, meta]) => (
            <div className="oq-ch-card" key={ch}>
              <div className="oq-ch-head">
                <span><i className={`ti ${meta.icon}`} /> {meta.label}</span>
                <span className={`oq-mode oq-mode--${plan[ch].mode}`}>{MODES.find(m => m.value === plan[ch].mode)?.label}</span>
              </div>
              <div className="oq-ch-body">
                <b>{plan[ch].cap_left}</b> of {plan[ch].daily_cap} left today
                <span className="oq-hint"> · {plan[ch].items.length} ready now</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {notice && (
        <div className={`oq-notice oq-notice--${notice.type}`}>
          <i className={`ti ${notice.type === "ok" ? "ti-check" : "ti-alert-circle"}`} />
          {notice.text}
        </div>
      )}

      <MessageApprovals onChanged={load} />

      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">Outreach queue</div>
            <div className="panel-sub">
              Each lead follows its profile's sequence on its own clock, inside its own working hours
            </div>
          </div>
          <div className="table-actions">
            <input className="filter-input oq-search" placeholder="Search…" value={search}
                   onChange={e => setSearch(e.target.value)} />
            <select className="filter-select" value={status} onChange={e => setStatus(e.target.value)}>
              <option value="queued">Queued</option>
              <option value="paused">Paused</option>
              <option value="removed">Removed</option>
            </select>
            <select className="filter-select" value={tier} onChange={e => setTier(e.target.value)}>
              <option value="">All tiers</option>
              <option value="hot">Hot</option>
              <option value="warm">Warm</option>
              <option value="cold">Cold</option>
            </select>
            <select className="filter-select" value={profile} onChange={e => setProfile(e.target.value)}>
              <option value="">All profiles</option>
              {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className={`btn-ghost ${dueOnly ? "oq-toggle--on" : ""}`} onClick={() => setDueOnly(v => !v)}>
              <i className="ti ti-bell-ringing" /> <span>Due now</span>
            </button>
            <button className="btn-ghost" onClick={sync} title="Add every Hot/Warm lead that is not queued yet">
              <i className="ti ti-refresh" /> <span>Find leads</span>
            </button>
            <button className="btn-primary" onClick={() => setRulesOpen(true)}>
              <i className="ti ti-settings" /> <span>Sending rules</span>
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Lead</th>
                <th>Tier</th>
                <th>Next step</th>
                <th>Due</th>
                <th>Their time</th>
                <th>Mode</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      {Array.from({ length: 7 }).map((_, j) => <td key={j}><div className="skeleton" /></td>)}
                    </tr>
                  ))
                : items.length === 0
                ? (
                    <tr>
                      <td colSpan={7} className="empty-state">
                        <i className="ti ti-list-check" />
                        <p>{dueOnly ? "Nothing is due right now" : `No ${status} leads`}</p>
                        <span>Hot and Warm leads join the queue automatically after they are scored</span>
                      </td>
                    </tr>
                  )
                : items.map(it => {
                    const ch = CHANNEL_META[it.next_channel];
                    const stopped = it.sequence_status === "stopped";
                    const finished = it.sequence_status === "completed";
                    return (
                      <tr key={it.lead_id}>
                        <td>
                          <div className="oq-name">{it.name}</div>
                          <div className="oq-sub">
                            {[it.city, it.profile_name].filter(Boolean).join(" · ")}
                          </div>
                          <div className="oq-contact">
                            {it.email && <span><i className="ti ti-mail" />{it.email}</span>}
                            {it.phone && <span><i className="ti ti-phone" />{it.phone}</span>}
                          </div>
                        </td>
                        <td>
                          <span className={`tier-badge ${tierClass(it.tier)}`}>{tierLabel(it.tier)}</span>
                          <div className="oq-sub">Score {it.score ?? "—"}</div>
                        </td>
                        <td>
                          {stopped ? (
                            <>
                              <span className="oq-stop"><i className="ti ti-hand-stop" /> Stopped: {it.stop_reason}</span>
                              {REPLY_LABELS[it.reply_label] && (
                                <div title={it.reply_summary ?? ""}>
                                  <span className={`oq-reply ${REPLY_LABELS[it.reply_label].cls}`}>
                                    {REPLY_LABELS[it.reply_label].text}
                                  </span>
                                </div>
                              )}
                              {it.revisit_at && <div className="oq-sub">Back on {shortDate(it.revisit_at)}</div>}
                            </>
                          ) : finished ? (
                            <span className="oq-sub">Sequence finished</span>
                          ) : ch ? (
                            <>
                              <div className="oq-step"><i className={`ti ${ch.icon}`} /> {it.next_step_label}</div>
                              <div className="oq-sub">Step {it.step_number} of {it.step_total}</div>
                            </>
                          ) : <span className="oq-sub">—</span>}
                        </td>
                        <td>
                          {stopped || finished || !it.next_due_at ? <span className="oq-sub">—</span> : (
                            <span className={`oq-due ${it.due_now ? "oq-due--now" : ""}`}>
                              {whenText(it.next_due_at, it.due_now)}
                            </span>
                          )}
                        </td>
                        <td>
                          <div className="oq-local">{it.lead_local_time}</div>
                          <div className="oq-sub">
                            {it.timezone ? it.timezone.split("/").pop().replace("_", " ") : "default zone"}
                            {!it.in_working_hours && " · closed"}
                          </div>
                        </td>
                        <td>
                          {it.mode && !stopped && !finished
                            ? <span className={`oq-mode oq-mode--${it.mode}`}>{MODES.find(m => m.value === it.mode)?.label}</span>
                            : <span className="oq-sub">—</span>}
                        </td>
                        <td>
                          <div className="oq-actions">
                            {status === "queued" && it.sequence_status === "active" && it.next_step_key && (
                              <>
                                <button className="oq-btn oq-btn--ok" title="This step is done: plan the next one"
                                        onClick={() => act(it.lead_id, "done", `Marked "${it.next_step_label}" as done`)}>
                                  <i className="ti ti-check" />
                                </button>
                                <button className="oq-btn" title="Skip this step"
                                        onClick={() => act(it.lead_id, "skip", `Skipped "${it.next_step_label}"`)}>
                                  <i className="ti ti-player-skip-forward" />
                                </button>
                              </>
                            )}
                            {status === "queued" && (
                              <button className="oq-btn" title="Pause" onClick={() => act(it.lead_id, "pause")}>
                                <i className="ti ti-player-pause" />
                              </button>
                            )}
                            {status === "paused" && (
                              <button className="oq-btn oq-btn--ok" title="Resume" onClick={() => act(it.lead_id, "resume")}>
                                <i className="ti ti-player-play" />
                              </button>
                            )}
                            {status === "removed" ? (
                              <button className="oq-btn oq-btn--ok" title="Add back to the queue"
                                      onClick={() => act(it.lead_id, "add", "Added back to the queue")}>
                                <i className="ti ti-arrow-back-up" />
                              </button>
                            ) : (
                              <button className="oq-btn oq-btn--danger" title="Remove from the queue"
                                      onClick={() => act(it.lead_id, "remove")}>
                                <i className="ti ti-trash" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
            </tbody>
          </table>
        </div>

        <div className="pagination">
          <div className="pag-info">
            Showing {data.total === 0 ? 0 : (page - 1) * LIMIT + 1}–{Math.min(page * LIMIT, data.total)} of {data.total.toLocaleString()} leads
          </div>
          <div className="pag-buttons">
            <button className="pag-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>‹</button>
            <button className="pag-btn active" disabled>{page}</button>
            <button className="pag-btn" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>›</button>
          </div>
        </div>
      </div>

      {rulesOpen && (
        <RulesModal onClose={() => setRulesOpen(false)}
                    onSaved={() => { setRulesOpen(false); flash("ok", "Sending rules saved"); load(); }} />
      )}
    </div>
  );
}
