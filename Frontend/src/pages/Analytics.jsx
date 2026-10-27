import { useState, useEffect, useCallback } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ComposedChart, Line, Area,
  ScatterChart, Scatter, ZAxis,
  Treemap,
  Cell, Legend,
} from "recharts";
import "./Analytics.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const OR     = "#e8622a";
const OR_DIM = "rgba(232,98,42,0.15)";
const BLUE   = "#3b82f6";
const GREEN  = "#22c55e";
const PURPLE = "#a855f7";
const AMBER  = "#f59e0b";
const PINK   = "#ec4899";
const CYAN   = "#06b6d4";
const TEAL   = "#14b8a6";
const ROSE   = "#f43f5e";

function ATip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="an-tip">
      {label && <div className="an-tip-label">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="an-tip-row">
          <span className="an-tip-dot" style={{ background: p.color || p.fill }} />
          <span className="an-tip-name">{p.name}</span>
          <span className="an-tip-val">
            {typeof p.value === "number" ? p.value.toLocaleString() : p.value}
          </span>
        </div>
      ))}
    </div>
  );
}

const MONTH_CHIPS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CHIP_W = 34; // fixed px width per chip — lets the slider position be computed, not measured

/**
 * MonthYearFilter — a single sliding capsule: year stepper on the left,
 * a month ribbon on the right with one glowing indicator that glides
 * between chips (instead of a plain dropdown or a row of toggle pills).
 * `year === null` means "all time" — the month ribbon is dimmed and inert
 * until a year is picked.
 */
function MonthYearFilter({ year, month, onChange }) {
  const now = new Date();
  const yearMin = now.getFullYear() - 4;
  const yearMax = now.getFullYear();
  const activeIdx = month ?? 0; // 0 = "All" (whole year), 1-12 = Jan..Dec

  const stepYear = (delta) => {
    if (year === null) { onChange(yearMax, null); return; }
    const next = year + delta;
    if (next < yearMin || next > yearMax) return;
    onChange(next, month);
  };

  return (
    <div className="my-filter">
      <div className="my-year">
        <button
          className="my-year-arrow" onClick={() => stepYear(-1)}
          disabled={year === null || year <= yearMin} aria-label="Previous year"
        >
          <i className="ti ti-chevron-left" />
        </button>
        <span className="my-year-label">{year ?? "All time"}</span>
        <button
          className="my-year-arrow" onClick={() => stepYear(1)}
          disabled={year !== null && year >= yearMax} aria-label="Next year"
        >
          <i className="ti ti-chevron-right" />
        </button>
        {year !== null && (
          <button className="my-year-clear" onClick={() => onChange(null, null)} aria-label="Clear filter" title="Back to all time">
            <i className="ti ti-x" />
          </button>
        )}
      </div>

      <div className="my-divider" />

      <div className={"my-month-track" + (year === null ? " my-month-track--disabled" : "")}>
        <div className="my-month-slider" style={{ transform: `translateX(${activeIdx * CHIP_W}px)` }} />
        {MONTH_CHIPS.map((label, i) => (
          <button
            key={label}
            className={"my-month-chip" + (activeIdx === i ? " my-month-chip--active" : "")}
            style={{ width: CHIP_W }}
            disabled={year === null}
            onClick={() => onChange(year, i === 0 ? null : i)}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function StatPill({ label, value, color, loading }) {
  return (
    <div className="an-stat-pill" style={{ borderColor: color + "33" }}>
      <span className="an-sp-val" style={{ color }}>
        {loading ? <span className="an-skel an-skel--sm" /> : value}
      </span>
      <span className="an-sp-label">{label}</span>
    </div>
  );
}

function RateGauge({ label, pct, color, icon, sub, loading }) {
  const r = 38;
  const circ = 2 * Math.PI * r;
  const dash = loading ? 0 : (pct / 100) * circ;
  return (
    <div className="an-gauge">
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="8" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8"
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          transform="rotate(-90 50 50)" style={{ transition: "stroke-dasharray 1.2s ease" }} />
        <text x="50" y="46" textAnchor="middle" fill={color} fontSize="14" fontWeight="800">{loading ? "—" : `${pct}%`}</text>
        <text x="50" y="60" textAnchor="middle" fill="rgba(255,255,255,0.3)" fontSize="9">{icon}</text>
      </svg>
      <div className="an-gauge-label">{label}</div>
      {sub && <div className="an-gauge-sub">{sub}</div>}
    </div>
  );
}

function HealthBar({ label, score, color }) {
  const w = Math.max(3, score);
  return (
    <div className="an-hbar">
      <div className="an-hbar-head">
        <span className="an-hbar-label">{label}</span>
        <span className="an-hbar-score" style={{ color }}>{score}</span>
      </div>
      <div className="an-hbar-track">
        <div className="an-hbar-fill" style={{ width: `${w}%`, background: color }} />
      </div>
    </div>
  );
}

function ChannelCard({ icon, color, bg, name, sent, replied, rate, loading }) {
  return (
    <div className="an-ch-card" style={{ borderColor: color + "22" }}>
      <div className="an-ch-top">
        <div className="an-ch-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
        <span className="an-ch-name">{name}</span>
      </div>
      <div className="an-ch-nums">
        <div className="an-ch-num">
          <span className="an-ch-val" style={{ color }}>
            {loading ? <span className="an-skel an-skel--sm" /> : (sent ?? 0).toLocaleString()}
          </span>
          <span className="an-ch-lbl">Sent</span>
        </div>
        <div className="an-ch-divider" />
        <div className="an-ch-num">
          <span className="an-ch-val" style={{ color }}>
            {loading ? <span className="an-skel an-skel--sm" /> : (replied ?? 0).toLocaleString()}
          </span>
          <span className="an-ch-lbl">Replied</span>
        </div>
        <div className="an-ch-divider" />
        <div className="an-ch-num">
          <span className="an-ch-val" style={{ color }}>
            {loading ? <span className="an-skel an-skel--sm" /> : `${rate ?? 0}%`}
          </span>
          <span className="an-ch-lbl">Reply Rate</span>
        </div>
      </div>
      <div className="an-ch-bar-track">
        <div className="an-ch-bar-fill" style={{ width: `${Math.min(rate ?? 0, 100)}%`, background: color }} />
      </div>
    </div>
  );
}

function Panel({ children, className = "" }) {
  return <div className={`an-panel ${className}`}>{children}</div>;
}
function PanelHead({ title, sub, right }) {
  return (
    <div className="an-panel-head">
      <div>
        <div className="an-panel-title">{title}</div>
        {sub && <div className="an-panel-sub">{sub}</div>}
      </div>
      {right && <div className="an-panel-right">{right}</div>}
    </div>
  );
}

const TM_COLORS = [OR, BLUE, GREEN, AMBER, PURPLE, PINK, CYAN, TEAL];
function CustomTreeContent(props) {
  const { x, y, width, height, name, value, depth, index } = props;
  if (width < 30 || height < 20) return null;
  const color = TM_COLORS[index % TM_COLORS.length];
  return (
    <g>
      <rect x={x} y={y} width={width} height={height}
        fill={color + "22"} stroke={color + "55"} strokeWidth={1} rx={6} />
      {width > 60 && height > 35 && (
        <>
          <text x={x + 10} y={y + 20} fill={color} fontSize={11} fontWeight={700}>{name}</text>
          <text x={x + 10} y={y + 34} fill="rgba(255,255,255,0.4)" fontSize={10}>{value}</text>
        </>
      )}
    </g>
  );
}

function ConvStep({ label, count, pct, color, isLast }) {
  return (
    <div className="an-conv-step">
      <div className="an-conv-bar-wrap">
        <div className="an-conv-bar" style={{ height: `${Math.max(8, pct)}%`, background: color }} />
      </div>
      <div className="an-conv-count" style={{ color }}>{count.toLocaleString()}</div>
      <div className="an-conv-pct">{pct}%</div>
      <div className="an-conv-label">{label}</div>
      {!isLast && <div className="an-conv-arrow"><i className="ti ti-chevron-right" /></div>}
    </div>
  );
}

export default function Analytics() {
  const [data,     setData]     = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [year,     setYear]     = useState(null);
  const [month,    setMonth]    = useState(null);
  const [lastSync, setLastSync] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (year !== null) {
        qs.set("year", year);
        if (month !== null) qs.set("month", month);
      }
      const params = qs.toString() ? `?${qs.toString()}` : "";
      const res = await fetch(`${API}/analytics/overview${params}`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setLastSync(new Date());
      }
    } catch (e) {
      console.error("Analytics fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [year, month]);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    const t = setInterval(fetchData, 90_000);
    return () => clearInterval(t);
  }, [fetchData]);

  // ── derived values ────────────────────────────────────────────────────────
  const total      = data?.total_leads     ?? 0;
  const hot        = data?.tiers?.hot      ?? 0;
  const warm       = data?.tiers?.warm     ?? 0;
  const cold       = data?.tiers?.cold     ?? 0;
  const emailSent  = data?.emails?.sent    ?? 0;
  const emailOpen  = data?.emails?.opened  ?? 0;
  const emailReply = data?.emails?.replied ?? 0;
  const openRate   = data?.emails?.open_rate  ?? 0;
  const replyRate  = data?.emails?.reply_rate ?? 0;
  const callTotal  = data?.calls?.total    ?? 0;
  const callInterested = data?.calls?.interested ?? 0;
  const callRate    = callTotal ? Math.round((callInterested / callTotal) * 100) : 0;
  const smsSent    = data?.whatsapp?.sent    ?? 0;   // backend key same, label → SMS
  const smsReply   = data?.whatsapp?.replied ?? 0;
  const smsRate    = smsSent ? Math.round((smsReply / smsSent) * 100) : 0;
  const dnc        = data?.dnc_total       ?? 0;
  const onboarded  = data?.onboarded       ?? 0;

  // Conversion funnel — WhatsApp → SMS
  const convSteps = [
    { label: "Scraped",   count: total,      color: OR     },
    { label: "Emailed",   count: emailSent,  color: BLUE   },
    { label: "Opened",    count: emailOpen,  color: CYAN   },
    { label: "Replied",   count: emailReply, color: GREEN  },
    { label: "Called",    count: callTotal,  color: AMBER  },
    { label: "SMS Sent",  count: smsSent,    color: PURPLE },  // WhatsApp → SMS
    { label: "Onboarded", count: onboarded,  color: TEAL   },
  ].map(s => ({
    ...s,
    pct: total > 0 ? Math.round((s.count / total) * 100) : 0,
  }));

  // Radar
  const tierData = [
    { subject: "Hot Leads",    value: hot,        fullMark: total || 1 },
    { subject: "Warm Leads",   value: warm,       fullMark: total || 1 },
    { subject: "Cold Leads",   value: cold,       fullMark: total || 1 },
    { subject: "Onboarded",    value: onboarded,  fullMark: total || 1 },
    { subject: "Email Reply",  value: emailReply, fullMark: emailSent || 1 },
    { subject: "SMS Reply",    value: smsReply,   fullMark: smsSent || 1 },  // WA → SMS
  ];

  // Channel comparison — WhatsApp → SMS
  const chanData = [
    { name: "Email", sent: emailSent, replied: emailReply,   rate: replyRate },
    { name: "Calls", sent: callTotal, replied: callInterested, rate: callRate },
    { name: "SMS",   sent: smsSent,   replied: smsReply,       rate: smsRate },  // WA → SMS
  ];

  // Treemap
  const treeData = [
    { name: "Hot",      size: hot        || 1 },
    { name: "Warm",     size: warm       || 1 },
    { name: "Cold",     size: cold       || 1 },
    { name: "Emailed",  size: emailSent  || 1 },
    { name: "Opened",   size: emailOpen  || 1 },
    { name: "Replied",  size: emailReply || 1 },
    { name: "Called",   size: callTotal  || 1 },
    { name: "SMS",      size: smsSent    || 1 },   // WA → SMS
    { name: "Onboard",  size: onboarded  || 1 },
  ];

  // Health scores
  const scores = {
    emailHealth: emailSent > 0 ? Math.min(100, Math.round(openRate * 1.5 + replyRate * 2)) : 0,
    smsHealth:   smsSent > 0   ? Math.min(100, smsRate * 1.5) : 0,              // waHealth → smsHealth
    tierHealth:  total > 0     ? Math.min(100, Math.round((hot / total) * 200 + (warm / total) * 100)) : 0,
    convHealth:  total > 0     ? Math.min(100, Math.round((onboarded / total) * 500)) : 0,
  };

  // Scatter
  const smsDelivered = data?.whatsapp?.delivered ?? 0;
  const smsDeliverRate = smsSent ? Math.round((smsDelivered / smsSent) * 100) : 0;
  const scatterData = [
    { x: openRate,      y: replyRate, z: emailSent, name: "Email" },
    { x: smsDeliverRate, y: smsRate,  z: smsSent,   name: "SMS"   },  // WA → SMS
    { x: callRate,       y: callRate, z: callTotal, name: "Calls" },
  ];

  const syncStr = lastSync
    ? lastSync.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) // browser's local timezone — no separate setting needed
    : "—";

  return (
    <div className="an-page">
      <div className="an-blobs">
        <div className="an-blob an-blob--1" />
        <div className="an-blob an-blob--2" />
        <div className="an-blob an-blob--3" />
      </div>

      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <div className="an-topbar">
        <div className="an-topbar-left">
          <div className="an-page-icon"><i className="ti ti-chart-dots-3" /></div>
          <div>
            <h1 className="an-page-title">Analytics</h1>
            <p className="an-page-sub">Performance insights across all channels</p>
          </div>
        </div>
        <div className="an-topbar-right">
          <div className="an-sync-badge">
            <span className="an-sync-dot" />
            <span>Synced {syncStr}</span>
          </div>
          <MonthYearFilter year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); }} />
          <button className="an-refresh-btn" onClick={fetchData} title="Refresh">
            <i className="ti ti-refresh" />
          </button>
        </div>
      </div>

      {/* ── Stat pills ── */}
      <div className="an-pills-row">
        <StatPill label="Total Leads"   value={total.toLocaleString()}     color={OR}     loading={loading} />
        <StatPill label="Emails Sent"   value={emailSent.toLocaleString()} color={BLUE}   loading={loading} />
        <StatPill label="Onboarded"     value={onboarded.toLocaleString()} color={TEAL}   loading={loading} />
        <StatPill label="DNC / Blocked" value={dnc.toLocaleString()}       color={ROSE}   loading={loading} />
        <StatPill label="SMS Replied"   value={smsReply.toLocaleString()}  color={GREEN}  loading={loading} />  {/* WA → SMS */}
      </div>

      {/* ── Row 1: Conversion Funnel + Radar ── */}
      <div className="an-row an-row--6-4">
        <Panel>
          <PanelHead
            title="Conversion Funnel"
            sub="Lead journey from scrape to onboarded"
            right={
              <span className="an-badge" style={{ background: OR_DIM, color: OR }}>
                {total > 0 ? `${convSteps[convSteps.length - 1].pct}% close rate` : "No data"}
              </span>
            }
          />
          <div className="an-panel-body">
            <div className="an-funnel-cols">
              {convSteps.map((s, i) => (
                <ConvStep key={s.label} {...s} isLast={i === convSteps.length - 1} />
              ))}
            </div>
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Performance Radar" sub="Multi-channel activity overview" />
          <div className="an-panel-body an-panel-body--center">
            {loading ? <span className="an-skel an-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={230}>
                <RadarChart data={tierData} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
                  <PolarGrid stroke="rgba(255,255,255,0.07)" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: "rgba(255,255,255,0.35)", fontSize: 10 }} />
                  <Radar name="Count" dataKey="value" stroke={OR} fill={OR} fillOpacity={0.18} strokeWidth={2} dot={{ fill: OR, r: 3 }} />
                  <Tooltip content={<ATip />} />
                </RadarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>
      </div>

      {/* ── Row 2: Rate Gauges + Channel Compare ── */}
      <div className="an-row an-row--5-5">
        <Panel>
          <PanelHead title="Outreach Rates" sub="Open rate, reply rate & SMS engagement" />
          <div className="an-panel-body">
            <div className="an-gauges-row">
              <RateGauge label="Email Open Rate"  pct={openRate}  color={CYAN}   icon="✉" sub={`${emailOpen} opens`}   loading={loading} />
              <RateGauge label="Email Reply Rate" pct={replyRate} color={GREEN}  icon="↩" sub={`${emailReply} replies`} loading={loading} />
              <RateGauge label="SMS Reply Rate"   pct={smsRate}   color={PURPLE} icon="💬" sub={`${smsReply} replies`}  loading={loading} />  {/* WA → SMS */}
            </div>
            <div className="an-rate-footer">
              <div className="an-rf-item">
                <i className="ti ti-mail" style={{ color: BLUE }} />
                <span>{emailSent.toLocaleString()} sent</span>
              </div>
              <div className="an-rf-item">
                <i className="ti ti-phone" style={{ color: AMBER }} />
                <span>{callTotal.toLocaleString()} calls</span>
              </div>
              <div className="an-rf-item">
                <i className="ti ti-message" style={{ color: GREEN }} />
                <span>{smsSent.toLocaleString()} SMS</span>  {/* WA icon → SMS icon */}
              </div>
            </div>
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Channel Comparison" sub="Sent vs replied by channel" />
          <div className="an-panel-body">
            {loading ? <span className="an-skel an-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={chanData} barGap={4} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <XAxis dataKey="name" tick={{ fill: "rgba(255,255,255,0.35)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "rgba(255,255,255,0.25)", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip content={<ATip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                  <Bar dataKey="sent"    name="Sent"    fill={BLUE}  radius={[4,4,0,0]} />
                  <Bar dataKey="replied" name="Replied" fill={GREEN} radius={[4,4,0,0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>
      </div>

      {/* ── Row 3: Health scores + Treemap ── */}
      <div className="an-row an-row--4-6">
        <Panel>
          <PanelHead title="Pipeline Health" sub="Automated score per area" />
          <div className="an-panel-body">
            <div className="an-health-list">
              <HealthBar label="Email Performance"  score={scores.emailHealth} color={CYAN}  />
              <HealthBar label="SMS Engagement"     score={scores.smsHealth}   color={GREEN} />  {/* WA → SMS */}
              <HealthBar label="Lead Tier Quality"  score={scores.tierHealth}  color={OR}    />
              <HealthBar label="Conversion Rate"    score={scores.convHealth}  color={TEAL}  />
            </div>
            <div className="an-health-note">
              <i className="ti ti-info-circle" />
              <span>Scores calculated from your live data</span>
            </div>
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Lead Distribution Map" sub="Size = volume across all pipeline stages" />
          <div className="an-panel-body">
            {loading ? <span className="an-skel an-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={220}>
                <Treemap data={treeData} dataKey="size" content={<CustomTreeContent />} />
              </ResponsiveContainer>
            )}
          </div>
        </Panel>
      </div>

      {/* ── Row 4: Tier breakdown + DNC card ── */}
      <div className="an-row an-row--7-3">
        <Panel>
          <PanelHead title="Tier Breakdown vs Conversion" sub="Hot / Warm / Cold leads with onboarded overlay" />
          <div className="an-panel-body">
            {loading ? <span className="an-skel an-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={220}>
                <ComposedChart
                  data={[
                    { name: "Hot",     count: hot,       flag: "hot"  },
                    { name: "Warm",    count: warm,      flag: "warm" },
                    { name: "Cold",    count: cold,      flag: "cold" },
                    { name: "Onboard", count: onboarded, flag: "on"   },
                  ]}
                  margin={{ top: 4, right: 4, left: -20, bottom: 0 }}
                >
                  <XAxis dataKey="name" tick={{ fill: "rgba(255,255,255,0.35)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "rgba(255,255,255,0.25)", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip content={<ATip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                  <Bar dataKey="count" name="Count" radius={[6,6,0,0]}>
                    <Cell fill={OR} /><Cell fill={AMBER} /><Cell fill={BLUE} /><Cell fill={TEAL} />
                  </Bar>
                  <Line type="monotone" dataKey="count" name="Trend" stroke={PINK} strokeWidth={2} dot={{ fill: PINK, r: 4 }} />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHead title="DNC Status" sub="Do Not Contact list" />
          <div className="an-panel-body an-panel-body--center">
            <div className="an-dnc-wrap">
              <div className="an-dnc-ring" style={{ borderColor: ROSE + "44" }}>
                <div className="an-dnc-inner">
                  <i className="ti ti-ban" style={{ color: ROSE, fontSize: 28 }} />
                  <span className="an-dnc-val" style={{ color: ROSE }}>{loading ? "—" : dnc.toLocaleString()}</span>
                  <span className="an-dnc-lbl">Blocked</span>
                </div>
              </div>
              <div className="an-dnc-stats">
                <div className="an-dnc-stat">
                  <span style={{ color: ROSE }}>{dnc}</span>
                  <span>Total DNC</span>
                </div>
                <div className="an-dnc-stat">
                  <span style={{ color: AMBER }}>
                    {total > 0 ? `${Math.round((dnc / (total + dnc)) * 100)}%` : "0%"}
                  </span>
                  <span>of universe</span>
                </div>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      {/* ── Row 5: Channel cards ── */}
      <div className="an-section-head">
        <h2 className="an-sec-title">Channel Performance Breakdown</h2>
        <p className="an-sec-sub">Detailed sent / reply / rate per outreach channel</p>
      </div>
      <div className="an-ch-grid">
        <ChannelCard icon="ti-mail"    color={BLUE}  bg="rgba(59,130,246,0.13)"  name="Email Outreach" sent={emailSent} replied={emailReply} rate={replyRate} loading={loading} />
        <ChannelCard icon="ti-phone"   color={AMBER} bg="rgba(245,158,11,0.13)"  name="VAPI Calls"     sent={callTotal} replied={callInterested} rate={callRate} loading={loading} />
        <ChannelCard icon="ti-message" color={GREEN} bg="rgba(34,197,94,0.13)"   name="SMS"            sent={smsSent}   replied={smsReply}   rate={smsRate}   loading={loading} />  {/* WA → SMS */}
      </div>
    </div>
  );
}