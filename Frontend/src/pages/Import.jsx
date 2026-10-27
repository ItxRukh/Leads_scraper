import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { downloadFile } from "../auth";
import "./Import.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const FIELD_LABELS = {
  business_name: "Business name", contact_name: "Contact name", contact_title: "Contact title", email: "Email",
  phone: "Phone", website: "Website", industry: "Industry", city: "City", state: "State", address: "Address",
  first_name: "First name", last_name: "Last name", notes: "Notes", ignore: "Ignore this column",
};
const MAPPABLE = ["business_name", "contact_name", "first_name", "last_name", "contact_title", "email", "phone",
  "website", "industry", "city", "state", "address", "notes", "ignore"];

const STATUS = {
  ready:     { label: "Ready",          tone: "ok" },
  duplicate: { label: "Duplicate",      tone: "warn" },
  dnc:       { label: "Do not contact", tone: "bad" },
  invalid:   { label: "Invalid",        tone: "bad" },
};

async function readError(res) {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `Something went wrong (${res.status})`;
}

function Chip({ tone, label, value }) {
  return (
    <div className={`im-chip im-chip--${tone}`}>
      <span className="im-chip-value">{value.toLocaleString()}</span>
      <span className="im-chip-label">{label}</span>
    </div>
  );
}

export default function Import() {
  const [industries, setIndustries] = useState([]);
  const [tab, setTab] = useState("file");
  const [file, setFile] = useState(null);
  const [text, setText] = useState("");
  const [batchName, setBatchName] = useState("");
  const [defaultIndustry, setDefaultIndustry] = useState("");
  const [useAi, setUseAi] = useState(true);
  const [skipNoEmail, setSkipNoEmail] = useState(false);

  const [preview, setPreview] = useState(null);
  const [mapDraft, setMapDraft] = useState({});
  const [busy, setBusy] = useState("");              // "", "preview", "commit"
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [batches, setBatches] = useState([]);
  const [batchError, setBatchError] = useState("");
  const dropRef = useRef(null);

  const labelFor = useMemo(() => Object.fromEntries(industries.map((i) => [i.key, i.label])), [industries]);

  const loadBatches = useCallback(() => {
    fetch(`${API}/imports`).then((r) => (r.ok ? r.json() : [])).then(setBatches).catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`${API}/imports/industries`).then((r) => (r.ok ? r.json() : [])).then(setIndustries).catch(() => {});
    loadBatches();
  }, [loadBatches]);

  const hasInput = tab === "file" ? !!file : text.trim().length > 0;

  async function runPreview(mapping) {
    setBusy("preview");
    setError("");
    setResult(null);
    const form = new FormData();
    if (tab === "file") form.append("file", file);
    else form.append("text", text);
    if (defaultIndustry) form.append("default_industry", defaultIndustry);
    form.append("use_ai", String(useAi));
    if (mapping) form.append("mapping", JSON.stringify(mapping));
    try {
      const res = await fetch(`${API}/imports/preview`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setPreview(data);
      setMapDraft(data.mapping);
      if (!batchName) {
        const stem = tab === "file" ? file.name.replace(/\.[^.]+$/, "") : "Pasted leads";
        setBatchName(`${stem} · ${new Date().toLocaleDateString()}`);
      }
    } catch (e) {
      setPreview(null);
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  const readyRows = useMemo(
    () => (preview?.rows ?? []).filter((r) => r.status === "ready" && !(skipNoEmail && !r.data.email)),
    [preview, skipNoEmail],
  );

  async function commit() {
    setBusy("commit");
    setError("");
    try {
      const res = await fetch(`${API}/imports/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_name: batchName || "Import",
          source: preview.source,
          default_industry: defaultIndustry || null,
          total_rows: preview.summary.total,
          ai_used: preview.ai_used,
          skip_no_email: skipNoEmail,
          rows: preview.rows.filter((r) => r.status === "ready")
            .map((r) => ({ ...r.data, row: r.row, notes: r.notes, extra: r.extra, ai_fixed: r.ai_fixed })),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setResult(await res.json());
      setPreview(null);
      setFile(null);
      setText("");
      setBatchName("");
      loadBatches();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function undo(batch) {
    if (!window.confirm(`Remove the ${batch.leads_now} leads from "${batch.name}"? This cannot be undone.`)) return;
    setBatchError("");
    const res = await fetch(`${API}/imports/${batch.id}`, { method: "DELETE" });
    if (!res.ok) setBatchError(await readError(res));
    loadBatches();
  }

  function onDrop(e) {
    e.preventDefault();
    dropRef.current?.classList.remove("im-drop--over");
    const f = e.dataTransfer.files?.[0];
    if (f) { setFile(f); setPreview(null); }
  }

  const mappingChanged = preview?.mode === "table" && JSON.stringify(mapDraft) !== JSON.stringify(preview.mapping);
  const s = preview?.summary;

  return (
    <div className="im-page">
      {/* ── 1. add leads ─────────────────────────────────────────────── */}
      <section className="im-card">
        <div className="im-card-head">
          <h2>Add leads</h2>
          <button className="im-link" onClick={() => downloadFile("/imports/sample.csv", "leads_sample.csv").catch((e) => setError(e.message))}>
            <i className="ti ti-download" /> Download sample CSV
          </button>
        </div>
        <p className="im-help">
          Needed: <b>business_name</b> and at least one of email, website or phone. Best results with
          <b> email, website, industry, city, state</b> too. Duplicates and Do-Not-Contact leads are caught for you.
        </p>

        <div className="im-tabs" role="tablist">
          <button role="tab" aria-selected={tab === "file"} className={tab === "file" ? "on" : ""} onClick={() => setTab("file")}>
            <i className="ti ti-file-upload" /> Upload file
          </button>
          <button role="tab" aria-selected={tab === "paste"} className={tab === "paste" ? "on" : ""} onClick={() => setTab("paste")}>
            <i className="ti ti-clipboard-text" /> Paste
          </button>
        </div>

        {tab === "file" ? (
          <label
            ref={dropRef}
            className="im-drop"
            onDragOver={(e) => { e.preventDefault(); dropRef.current?.classList.add("im-drop--over"); }}
            onDragLeave={() => dropRef.current?.classList.remove("im-drop--over")}
            onDrop={onDrop}
          >
            <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); }} />
            <i className="ti ti-cloud-upload" />
            {file ? <span><b>{file.name}</b> · {(file.size / 1024).toFixed(0)} KB</span>
                  : <span>Drop a <b>.csv</b> or <b>.xlsx</b> file here, or click to choose</span>}
          </label>
        ) : (
          <textarea className="im-text" rows={8} value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }}
            placeholder={"Paste rows copied from Excel or Google Sheets, with the column names on the first line:\n\nbusiness_name\temail\twebsite\tindustry\tcity\tstate"} />
        )}

        <div className="im-options">
          <label>Default industry
            <select value={defaultIndustry} onChange={(e) => setDefaultIndustry(e.target.value)}>
              <option value="">Use the file's industry column</option>
              {industries.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
            </select>
          </label>
          <label className="im-check">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            Use AI to tidy columns and tag industries
          </label>
        </div>

        {error && <div className="im-error" role="alert">{error}</div>}
        <button className="im-btn" disabled={!hasInput || busy !== ""} onClick={() => runPreview()}>
          {busy === "preview" ? "Reading…" : "Preview"}
        </button>
      </section>

      {/* ── result of the last import ─────────────────────────────────── */}
      {result && (
        <section className="im-card im-done">
          <h2><i className="ti ti-circle-check" /> Imported {result.imported.toLocaleString()} lead{result.imported === 1 ? "" : "s"}</h2>
          <p className="im-help">
            Skipped: {result.duplicates} duplicate · {result.on_dnc} on the Do-Not-Contact list · {result.invalid} invalid
            {result.skipped ? ` · ${result.skipped} without email` : ""}.
          </p>
          <Link className="im-btn" to="/admin/leads">View leads</Link>
        </section>
      )}

      {/* ── 2. preview ────────────────────────────────────────────────── */}
      {preview && (
        <section className="im-card">
          <div className="im-card-head"><h2>Preview</h2><span className="im-help">{preview.source}</span></div>

          <div className="im-chips">
            <Chip tone="neutral" label="Rows" value={s.total} />
            <Chip tone="ok" label="Ready" value={s.ready} />
            <Chip tone="warn" label="Duplicates" value={s.duplicate} />
            <Chip tone="bad" label="Do not contact" value={s.dnc} />
            <Chip tone="bad" label="Invalid" value={s.invalid} />
            <Chip tone="neutral" label="Ready, no email" value={s.no_email} />
          </div>

          {preview.notes.length > 0 && (
            <ul className="im-notes">{preview.notes.map((n) => <li key={n}><i className="ti ti-sparkles" /> {n}</li>)}</ul>
          )}

          {preview.mode === "table" && (
            <details className="im-map" open={mappingChanged}>
              <summary>Column mapping ({Object.values(preview.mapping).filter((f) => f !== "ignore").length} of {preview.headers.length} columns used)</summary>
              <div className="im-map-grid">
                {preview.headers.map((h, i) => (
                  <label key={i}>
                    <span title={h}>{h || `Column ${i + 1}`}</span>
                    <select value={mapDraft[i] ?? "ignore"} onChange={(e) => setMapDraft({ ...mapDraft, [i]: e.target.value })}>
                      {MAPPABLE.map((f) => <option key={f} value={f}>{FIELD_LABELS[f]}</option>)}
                    </select>
                  </label>
                ))}
              </div>
              {mappingChanged && (
                <button className="im-btn im-btn--ghost" disabled={busy !== ""}
                  onClick={() => runPreview(Object.fromEntries(Object.entries(mapDraft).filter(([, f]) => f !== "ignore")))}>
                  Update preview
                </button>
              )}
            </details>
          )}

          <div className="im-table-wrap">
            <table className="im-table">
              <thead>
                <tr><th>Row</th><th>Status</th><th>Business</th><th>Contact</th><th>Email</th><th>Phone</th><th>Website</th><th>Industry</th><th>Where</th></tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 100).map((r) => {
                  const st = STATUS[r.status];
                  const notes = [...r.reasons, ...r.warnings];
                  return (
                    <tr key={r.row} className={r.status !== "ready" ? "im-dim" : ""}>
                      <td>{r.row}</td>
                      <td>
                        <span className={`im-status im-status--${st.tone}`}>{st.label}</span>
                        {notes.length > 0 && <div className="im-why">{notes.join(" · ")}</div>}
                      </td>
                      <td>{r.data.business_name || "—"}</td>
                      <td>{r.data.contact_name || "—"}</td>
                      <td>{r.data.email || "—"}{r.data.email_is_role && <span className="im-tag" title="Shared mailbox such as info@ or sales@">role</span>}</td>
                      <td>{r.data.phone || "—"}</td>
                      <td>{r.data.website ? r.data.website.replace(/^https?:\/\//, "") : "—"}</td>
                      <td>{labelFor[r.data.industry] || "—"}{r.ai_fixed.includes("industry") && <span className="im-tag" title="Tagged by AI">AI</span>}</td>
                      <td>{[r.data.city, r.data.state].filter(Boolean).join(", ") || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {preview.rows.length > 100 && <p className="im-help">Showing the first 100 of {preview.rows.length.toLocaleString()} rows. All ready rows are imported.</p>}

          <div className="im-commit">
            <label>Batch name
              <input value={batchName} onChange={(e) => setBatchName(e.target.value)} maxLength={200} />
            </label>
            <label className="im-check">
              <input type="checkbox" checked={skipNoEmail} onChange={(e) => setSkipNoEmail(e.target.checked)} />
              Import only leads that have an email
            </label>
            <button className="im-btn" disabled={readyRows.length === 0 || busy !== ""} onClick={commit}>
              {busy === "commit" ? "Importing…" : `Import ${readyRows.length.toLocaleString()} lead${readyRows.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </section>
      )}

      {/* ── earlier imports ───────────────────────────────────────────── */}
      <section className="im-card">
        <div className="im-card-head"><h2>Earlier imports</h2></div>
        {batchError && <div className="im-error" role="alert">{batchError}</div>}
        {batches.length === 0 ? <p className="im-help">Nothing imported yet.</p> : (
          <div className="im-table-wrap">
            <table className="im-table">
              <thead><tr><th>Name</th><th>Source</th><th>Date</th><th>Imported</th><th>In leads now</th><th>Skipped</th><th /></tr></thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id}>
                    <td>{b.name}</td>
                    <td>{b.source}</td>
                    <td>{b.created_at ? new Date(b.created_at).toLocaleString() : "—"}</td>
                    <td>{b.imported}</td>
                    <td>{b.leads_now}</td>
                    <td>{b.duplicates + b.on_dnc + b.invalid}</td>
                    <td>{b.leads_now > 0 && <button className="im-link im-link--bad" onClick={() => undo(b)}>Undo</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
