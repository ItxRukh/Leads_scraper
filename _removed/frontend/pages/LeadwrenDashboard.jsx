import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./Dashboard.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const LABELS = { interested: "Interested", question: "Has a question", not_now: "Not now", stop: "Asked to stop", other: "Other" };

function Stat({ label, value, hint, tone, to }) {
  const body = (
    <>
      <span className="dbn-stat-label">{label}</span>
      <span className={`dbn-stat-value${tone ? ` dbn-stat-value--${tone}` : ""}`}>{value.toLocaleString()}</span>
      {hint && <span className="dbn-stat-hint">{hint}</span>}
    </>
  );
  return to ? <Link className="dbn-stat" to={to}>{body}</Link> : <div className="dbn-stat">{body}</div>;
}

const pct = (part, whole) => (whole ? `${Math.round((part / whole) * 100)}%` : "–");

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

  const { funnel, tiers } = data;
  const count = (key) => funnel.find((f) => f.key === key)?.count ?? 0;
  const top = Math.max(...funnel.map((f) => f.count), 1);

  return (
    <div className="dbn-page">
      <section className="dbn-card dbn-todo">
        <h2>{(data.todo ?? []).length ? "What needs you now" : "All caught up"}</h2>
        {(data.todo ?? []).length === 0 ? (
          <p className="dbn-muted">Nothing is waiting. New leads, drafts and replies will show up here.</p>
        ) : (
          <ul>
            {(data.todo ?? []).map((item) => (
              <li key={item.text}>
                <Link to={item.to} className={`dbn-todo-item dbn-todo-item--${item.tone}`}>
                  <span className="dbn-dot" />
                  <span>{item.text}</span>
                  <span className="dbn-todo-go">Open →</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="dbn-stats">
        <Stat label="Leads" value={count("imported")} hint={`${tiers.hot} hot · ${tiers.warm} warm · ${tiers.cold} cold`} to="/admin/leads" />
        <Stat label="Emailed" value={count("emailed")} hint={`${data.today.emails} handed over today`} />
        <Stat label="Replied" value={count("replied")} hint={`${pct(count("replied"), count("emailed"))} of emailed`} tone="ok" />
        <Stat label="In GoHighLevel" value={count("ghl")} hint={`${count("interested")} said interested`} tone="ok" />
        <Stat label="Do not contact" value={data.do_not_contact} hint={`${data.bounced} bounced`} to="/admin/dnc" />
      </div>

      <div className="dbn-two">
        <section className="dbn-card">
          <h2>From import to GoHighLevel</h2>
          <div className="dbn-funnel">
            {funnel.map((f) => (
              <div className="dbn-funnel-row" key={f.key}>
                <span className="dbn-funnel-label">{f.label}</span>
                <span className="dbn-funnel-track"><span className="dbn-funnel-bar" style={{ width: `${(f.count / top) * 100}%` }} /></span>
                <span className="dbn-funnel-count">{f.count.toLocaleString()}</span>
              </div>
            ))}
          </div>
          <p className="dbn-muted">{data.waiting_in_instantly} lead{data.waiting_in_instantly === 1 ? "" : "s"} are in the Instantly campaign, receiving their follow-ups.</p>
        </section>

        <section className="dbn-card">
          <h2>Recent replies</h2>
          {data.recent_replies.length === 0 ? <p className="dbn-muted">No replies yet. When someone answers, you see who and from which industry here.</p> : (
            <ul className="dbn-replies">
              {data.recent_replies.map((r, i) => (
                <li key={i}>
                  <div className="dbn-reply-head">
                    <Link to="/admin/leads" className="dbn-reply-name">{r.lead_name}</Link>
                    <span className="dbn-chip">{r.industry}</span>
                    <span className={`dbn-tag dbn-tag--${r.label ?? "other"}`}>{LABELS[r.label] ?? "Not read yet"}</span>
                    {r.ghl === "sent" && <span className="dbn-tag dbn-tag--ghl">In GoHighLevel</span>}
                  </div>
                  <div className="dbn-muted">{r.summary || r.text}</div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="dbn-card">
        <h2>By industry</h2>
        {data.by_industry.length === 0 ? <p className="dbn-muted">Import some leads to see them here.</p> : (
          <table className="dbn-table">
            <thead><tr><th>Industry</th><th>Leads</th><th>Hot</th><th>Emailed</th><th>Replied</th></tr></thead>
            <tbody>
              {data.by_industry.map((r) => (
                <tr key={r.industry}><td>{r.industry}</td><td>{r.leads}</td><td>{r.hot}</td><td>{r.emailed}</td><td>{r.replied}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
