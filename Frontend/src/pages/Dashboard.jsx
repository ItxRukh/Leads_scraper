import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./Dashboard.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const QUALITY = [
  { key: "valid",       label: "Valid",          tone: "ok" },
  { key: "catch_all",   label: "Catch-all",      tone: "warn" },
  { key: "unknown",     label: "Not certain",    tone: "info" },
  { key: "invalid",     label: "Invalid",        tone: "bad" },
  { key: "not_checked", label: "Not checked",    tone: "muted" },
  { key: "no_email",    label: "No email",       tone: "muted" },
];

const num = (n) => (n ?? 0).toLocaleString();
const pct = (part, whole) => (whole ? `${Math.round((part / whole) * 100)}%` : "–");
const when = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—");

function Stat({ label, value, hint, tone, to }) {
  const body = (
    <>
      <span className="dbn-stat-label">{label}</span>
      <span className={`dbn-stat-value${tone ? ` dbn-stat-value--${tone}` : ""}`}>{num(value)}</span>
      {hint && <span className="dbn-stat-hint">{hint}</span>}
    </>
  );
  return to ? <Link className="dbn-stat" to={to}>{body}</Link> : <div className="dbn-stat">{body}</div>;
}

function Card({ title, sub, children, wide }) {
  return (
    <section className={`dbn-card${wide ? " dbn-card--wide" : ""}`}>
      <h2>{title}</h2>
      {sub && <p className="dbn-sub">{sub}</p>}
      {children}
    </section>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => fetch(`${API}/dashboard/summary`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Could not load the dashboard (${r.status})`))))
      .then(setData)
      .catch((e) => setError(e.message));
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, []);

  if (error && !data) return <div className="dbn-page"><div className="dbn-error">{error}</div></div>;
  if (!data) return <div className="dbn-page"><p className="dbn-muted">Loading…</p></div>;

  const { totals, funnel, email_quality: quality, weakness, ghl, dnc } = data;
  const top = Math.max(...funnel.map((f) => f.count), 1);
  const qualityTotal = Object.values(quality).reduce((a, b) => a + b, 0);
  const dayMax = Math.max(...data.daily_imports.map((d) => d.leads), 1);

  return (
    <div className="dbn-page">
      <Card title={data.todo.length ? "What needs you now" : "All caught up"}>
        {data.todo.length === 0 ? (
          <p className="dbn-muted">Nothing is waiting. New leads and drafts will show up here.</p>
        ) : (
          <ul className="dbn-todo">
            {data.todo.map((item) => {
              const inner = (
                <>
                  <span className="dbn-dot" />
                  <span>{item.text}</span>
                  {item.to && <span className="dbn-todo-go">Open →</span>}
                </>
              );
              return (
                <li key={item.key}>
                  {item.to
                    ? <Link to={item.to} className={`dbn-todo-item dbn-todo-item--${item.tone}`}>{inner}</Link>
                    : <div className={`dbn-todo-item dbn-todo-item--${item.tone}`}>{inner}</div>}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <div className="dbn-stats">
        <Stat label="Leads" value={totals.leads} hint={`${totals.imports} import${totals.imports === 1 ? "" : "s"}`} to="/admin/leads" />
        <Stat label="Drafts waiting" value={totals.drafts} hint="on Approve emails" tone="warn" to="/admin/approve" />
        <Stat label="Approved" value={totals.approved} hint="first emails" tone="ok" />
        <Stat label="In GoHighLevel" value={ghl.in_ghl} hint={ghl.configured ? "pushed" : "not connected"} />
        <Stat label="Replied" value={ghl.replied} hint={ghl.configured ? `${pct(ghl.replied, ghl.in_ghl)} of those in GHL` : "from GoHighLevel"} tone="ok" />
        <Stat label="Do not contact" value={totals.do_not_contact} to="/admin/dnc" />
      </div>

      <div className="dbn-two">
        <Card title="From import to reply" sub="How many leads reached each step">
          <div className="dbn-funnel">
            {funnel.map((f, i) => (
              <div className="dbn-funnel-row" key={f.key}>
                <span className="dbn-funnel-label">{f.label}</span>
                <span className="dbn-funnel-track"><span className="dbn-funnel-bar" style={{ width: `${(f.count / top) * 100}%` }} /></span>
                <span className="dbn-funnel-count">{num(f.count)}</span>
                <span className="dbn-funnel-pct">{i === 0 ? "" : pct(f.count, funnel[0].count)}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Email addresses" sub={`${num(qualityTotal)} leads`}>
          {qualityTotal === 0 ? <p className="dbn-muted">No leads yet.</p> : (
            <>
              <div className="dbn-seg">
                {QUALITY.map((q) => quality[q.key] > 0 && (
                  <span key={q.key} className={`dbn-seg-part dbn-seg-part--${q.tone}`} style={{ flex: quality[q.key] }} title={`${q.label}: ${quality[q.key]}`} />
                ))}
              </div>
              <ul className="dbn-legend">
                {QUALITY.map((q) => (
                  <li key={q.key}><span className={`dbn-swatch dbn-swatch--${q.tone}`} />{q.label}<b>{num(quality[q.key])}</b></li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      <div className="dbn-three">
        <Card title="Weakness" sub="What each first email talks about">
          <ul className="dbn-legend">
            <li><span className="dbn-swatch dbn-swatch--ok" />Leads with a weakness<b>{num(weakness.with_weakness)}</b></li>
            <li><span className="dbn-swatch dbn-swatch--info" />Corrected by the AI<b>{num(weakness.edited)}</b></li>
            <li><span className="dbn-swatch dbn-swatch--muted" />Without a weakness<b>{num(weakness.without)}</b></li>
          </ul>
        </Card>

        <Card title="Leads imported" sub="Last 14 days">
          <div className="dbn-bars">
            {data.daily_imports.map((d) => (
              <span key={d.date} className="dbn-bar" title={`${d.date}: ${d.leads}`}>
                <span className="dbn-bar-fill" style={{ height: `${(d.leads / dayMax) * 100}%` }} />
              </span>
            ))}
          </div>
          <div className="dbn-bars-axis"><span>{when(data.daily_imports[0].date)}</span><span>today</span></div>
        </Card>

        <Card title="GoHighLevel" sub={ghl.configured ? "What GoHighLevel reported back" : "Not connected yet"}>
          {ghl.configured ? (
            <ul className="dbn-legend">
              <li><span className="dbn-swatch dbn-swatch--info" />Emailed<b>{num(ghl.sent)}</b></li>
              <li><span className="dbn-swatch dbn-swatch--ok" />Opened<b>{num(ghl.opened)}</b></li>
              <li><span className="dbn-swatch dbn-swatch--ok" />Replied<b>{num(ghl.replied)}</b></li>
              <li><span className="dbn-swatch dbn-swatch--bad" />Bounced<b>{num(ghl.bounced)}</b></li>
              <li><span className="dbn-swatch dbn-swatch--bad" />Unsubscribed<b>{num(ghl.unsubscribed)}</b></li>
            </ul>
          ) : (
            <p className="dbn-muted">Add <code>GHL_API_TOKEN</code> and <code>GHL_LOCATION_ID</code> to <code>Backend/.env</code>. Emailed, opened, replied, bounced and unsubscribed numbers appear here.</p>
          )}
        </Card>
      </div>

      <div className="dbn-two">
        <Card title="By industry">
          {data.by_industry.length === 0 ? <p className="dbn-muted">Import some leads to see them here.</p> : (
            <table className="dbn-table">
              <thead><tr><th>Industry</th><th>Leads</th><th>Email OK</th><th>Drafts</th><th>Approved</th><th>In GHL</th></tr></thead>
              <tbody>
                {data.by_industry.map((r) => (
                  <tr key={r.industry}><td>{r.industry}</td><td>{r.leads}</td><td>{r.email_ok}</td><td>{r.drafts}</td><td>{r.approved}</td><td>{r.in_ghl}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Do not contact" sub={`${num(dnc.total)} on the list`}>
          {dnc.total === 0 ? <p className="dbn-muted">Nobody is blocked yet.</p> : (
            <>
              <ul className="dbn-legend">
                {dnc.groups.map((g) => <li key={g.label}><span className="dbn-swatch dbn-swatch--bad" />{g.label}<b>{num(g.count)}</b></li>)}
              </ul>
              <ul className="dbn-recent">
                {dnc.recent.map((r, i) => <li key={i}><span>{r.who}</span><span className="dbn-muted">{when(r.at)}</span></li>)}
              </ul>
            </>
          )}
        </Card>
      </div>

      <Card title="Imports" sub="The latest ones" wide>
        {data.by_import.length === 0 ? <p className="dbn-muted">Nothing imported yet.</p> : (
          <table className="dbn-table">
            <thead><tr><th>Name</th><th>Date</th><th>Leads</th><th>Checked</th><th>Emails written</th><th>Approved</th><th>Skipped</th></tr></thead>
            <tbody>
              {data.by_import.map((b) => (
                <tr key={b.id}><td>{b.name}</td><td>{when(b.at)}</td><td>{b.leads}</td><td>{b.checked}</td><td>{b.written}</td><td>{b.approved}</td><td>{b.skipped}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
