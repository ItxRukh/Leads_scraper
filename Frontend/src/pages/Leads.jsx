import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie, AreaChart, Area, CartesianGrid,
} from "recharts";
import "./Leads.css";
import LeadDetailModal from "../components/LeadDetailModal";
import { getSettings } from "./Settings";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── Niche list ───────────────────────────────────────────────────────────────
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

// ─── helpers ──────────────────────────────────────────────────────────────────
function tierClass(tier) {
  const map = { hot: "tier-hot", warm: "tier-warm", cold: "tier-cold" };
  return map[tier] ?? "tier-unscored";
}
function tierLabel(tier) {
  const map = { hot: "🔥 Hot", warm: "Warm", cold: "Cold" };
  return map[tier] ?? "Unscored";
}
function emailBadge(status) {
  const map = {
    valid:       { cls: "email-valid",    label: "✓ Valid" },
    deliverable: { cls: "email-valid",    label: "✓ Valid" },
    "catch-all": { cls: "email-catchall", label: "~ Catch-all" },
    catch_all:   { cls: "email-catchall", label: "~ Catch-all" },
    invalid:     { cls: "email-invalid",  label: "✗ Invalid" },
    unknown:     { cls: "email-unknown",  label: "? Unknown" },
  };
  return map[status] ?? { cls: "email-unknown", label: status ?? "—" };
}
function srcColor(src) {
  const map = {
    google_places: "#e8622a",
    serpapi:       "#7c3aed",
    yelp:          "#2563eb",
    manual:        "#059669",
  };
  return map[src] ?? "#999";
}
function srcLabel(src) {
  const map = {
    google_places: "Google Places",
    serpapi:       "SerpAPI",
    yelp:          "Yelp",
    manual:        "Manual",
  };
  return map[src] ?? (src ?? "—");
}
const NICHE_PALETTE = ["#e8622a", "#7c3aed", "#2563eb", "#059669", "#f59e0b", "#dc2626", "#0ea5e9", "#a855f7"];
function nicheColor(i) {
  return NICHE_PALETTE[i % NICHE_PALETTE.length];
}
function stageLabel(s) {
  if (!s) return "—";
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const MONTH_CHIPS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CHIP_W = 34; // fixed px width per chip — lets the slider position be computed, not measured

/**
 * MonthYearFilter — sliding capsule: a year stepper on the left, and a
 * month ribbon on the right with a single glowing pill that glides
 * between chips. Filters both the charts/stat cards above and the
 * leads table below by `scraped_at`. `year === null` means "all time".
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

// ─── CitySearch component ─────────────────────────────────────────────────────
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

// ─── NicheSelect component ────────────────────────────────────────────────────
function NicheSelect({ value, onChange, placeholder = "All niches" }) {
  const [query, setQuery]   = useState("");
  const [open, setOpen]     = useState(false);
  const wrapRef             = useRef(null);

  // close on outside click
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

  // label shown on trigger button
  const displayLabel = value
    ? NICHES.find((n) => n.toLowerCase() === value) ?? value
    : placeholder;

  return (
    <div className="niche-select-wrap" ref={wrapRef}>
      {/* trigger */}
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

      {/* dropdown */}
      {open && (
        <div className="niche-dropdown">
          {/* search inside dropdown */}
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
            {/* "All niches" option */}
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

// ─── sub-components ───────────────────────────────────────────────────────────
function StatCard({ icon, color, bg, label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-top">
        <div className="sc-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
      </div>
      <div className="sc-value" style={{ color: "#fff" }}>
        {loading ? (
          <span className="skeleton" style={{ width: 60, display: "inline-block" }} />
        ) : (
          (typeof value === "number" ? value.toLocaleString() : value) ?? "0"
        )}
      </div>
      <div className="sc-label">{label}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

function ScoreBar({ score, incomplete, incompleteReason }) {
  const s = score ?? 0;
  const color = s >= 85 ? "#e8622a" : s >= 40 ? "#f59e0b" : "#3b82f6";
  return (
    <div className="score-bar-wrap">
      <div className="score-track">
        <div className="score-fill" style={{ width: `${Math.min(s, 100)}%`, background: color }} />
      </div>
      <span className="score-num" style={{ color }}>{s}</span>
      {incomplete && (
        <i
          className="ti ti-alert-triangle score-incomplete-icon"
          title={incompleteReason || "Score incomplete — website couldn't be checked"}
        />
      )}
    </div>
  );
}

const CUSTOM_TOOLTIP = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="ct-label">{label}</div>
      <div className="ct-val">{payload[0].value.toLocaleString()} leads</div>
    </div>
  );
};

// ─── main page ────────────────────────────────────────────────────────────────
export default function Leads() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [leads,      setLeads]      = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [total,      setTotal]      = useState(0);

  const [chartLoad,  setChartLoad]  = useState(true);
  const [totalLeads, setTotalLeads] = useState(0);
  const [hotCount,   setHotCount]   = useState(0);
  const [verified,   setVerified]   = useState(0);
  const [onboarded,  setOnboarded]  = useState(0);
  const [sourceData, setSourceData] = useState([]);
  const [nicheData,  setNicheData]  = useState([]);
  const [timeData,   setTimeData]   = useState([]);
  const [tierData,   setTierData]   = useState([]);
  const [funnelData, setFunnelData] = useState([]);

  const [tier,     setTier]     = useState("");
  const [niche,    setNiche]    = useState("");
  const [city,     setCity]     = useState("");
  const [year,     setYear]     = useState(null);
  const [month,    setMonth]    = useState(null);
  const [stuckOnly, setStuckOnly] = useState(false); // Feature 6 — "Stuck Leads" quick filter
  const [page,     setPage]     = useState(1);
  const [selected, setSelected] = useState([]);
  const LIMIT = 10;

  const [scraperOpen, setScraperOpen] = useState(false);
  const [scrapeForm,  setScrapeForm]  = useState(() => {
    const s = getSettings();
    return {
      niche: s.defaultNiche || "", city: s.defaultCity || "",
      min_rating: 3.5, min_reviews: 10,
    };
  });
  const [scraping, setScraping] = useState(false);
  const [viewLeadId, setViewLeadId] = useState(null);

  // Deep link support: arriving via /admin/leads?lead=<id> (e.g. from a
  // notification bell click) opens that lead's detail modal directly —
  // no need to filter/scroll the table to find it.
  useEffect(() => {
    const id = searchParams.get("lead");
    if (id) setViewLeadId(Number(id));
    if (searchParams.get("stuck") === "true") setStuckOnly(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip:  String((page - 1) * LIMIT),
        limit: String(LIMIT),
        ...(tier  && { tier }),
        ...(niche && { niche }),
        ...(city  && { city }),
        ...(year !== null && { year: String(year) }),
        ...(year !== null && month !== null && { month: String(month) }),
        ...(stuckOnly && { stuck: "true" }),
      });
      const res  = await fetch(`${API}/leads?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLeads(data.leads ?? []);
      setTotal(data.total ?? 0);
    } catch (e) {
      console.error("fetchLeads:", e);
      setLeads([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, tier, niche, city, year, month, stuckOnly]);

  const fetchCharts = useCallback(async () => {
    setChartLoad(true);
    try {
      const dq = new URLSearchParams();
      if (year !== null) {
        dq.set("year", year);
        if (month !== null) dq.set("month", month);
      }
      const dateParams = dq.toString() ? `&${dq.toString()}` : "";

      const [overviewRes, pipelineRes, allLeadsRes] = await Promise.all([
        fetch(`${API}/analytics/overview${dateParams ? `?${dq.toString()}` : ""}`),
        fetch(`${API}/pipeline/summary`),
        fetch(`${API}/leads?limit=9999${dateParams}`),
      ]);

      if (overviewRes.ok) {
        const ov = await overviewRes.json();
        setTotalLeads(ov.total_leads ?? 0);
        setHotCount(ov.tiers?.hot ?? 0);
        setOnboarded(ov.onboarded ?? 0);
        setTierData([
          { name: "Hot",  value: ov.tiers?.hot  ?? 0, color: "#e8622a" },
          { name: "Warm", value: ov.tiers?.warm ?? 0, color: "#f59e0b" },
          { name: "Cold", value: ov.tiers?.cold ?? 0, color: "#3b82f6" },
        ]);
      }

      if (pipelineRes.ok) {
        const ps = await pipelineRes.json();
        setFunnelData([
          { name: "Scraped",    value: ps.scraped      ?? 0, fill: "#e8622a" },
          { name: "Audited",    value: ps.audited      ?? 0, fill: "#f97316" },
          { name: "Email Sent", value: ps.email_sent   ?? 0, fill: "#f59e0b" },
          { name: "Opened",     value: ps.email_opened ?? 0, fill: "#84cc16" },
          { name: "Replied",    value: ps.replied      ?? 0, fill: "#22c55e" },
          { name: "Onboarded",  value: ps.onboarded    ?? 0, fill: "#3b82f6" },
        ]);
      }

      if (allLeadsRes.ok) {
        const allData = await allLeadsRes.json();
        const all = allData.leads ?? [];
        const srcMap = {};
        all.forEach((l) => {
          const k = l.source ?? "manual";
          srcMap[k] = (srcMap[k] ?? 0) + 1;
        });
        setSourceData(
          Object.entries(srcMap).map(([name, value]) => ({
            name: srcLabel(name),
            value,
            color: srcColor(name),
          }))
        );
        setVerified(all.filter((l) => l.email_verified).length);

        // ── Leads by niche (top 6, rest bucketed as "Other") ──────────────
        const nicheMap = {};
        all.forEach((l) => {
          const k = l.niche?.trim() || "Uncategorized";
          nicheMap[k] = (nicheMap[k] ?? 0) + 1;
        });
        const sortedNiches = Object.entries(nicheMap).sort((a, b) => b[1] - a[1]);
        const topNiches = sortedNiches.slice(0, 6);
        const otherTotal = sortedNiches.slice(6).reduce((s, [, v]) => s + v, 0);
        const nicheRows = topNiches.map(([name, value], i) => ({
          name, value, color: nicheColor(i),
        }));
        if (otherTotal > 0) {
          nicheRows.push({ name: "Other", value: otherTotal, color: "#666" });
        }
        setNicheData(nicheRows);

        // ── Leads over time — adapts to the active filter ─────────────────
        // No filter  -> last 14 days.  Year only -> 12 months of that year.
        // Year+month -> every day of that month.
        const dayMap = {};
        all.forEach((l) => {
          if (!l.scraped_at) return;
          const key = l.scraped_at.slice(0, 10); // YYYY-MM-DD
          dayMap[key] = (dayMap[key] ?? 0) + 1;
        });

        let days;
        if (year !== null && month === null) {
          // whole year selected — bucket by month
          const monthTotals = new Array(12).fill(0);
          Object.entries(dayMap).forEach(([key, count]) => {
            const [y, m] = key.split("-");
            if (Number(y) === year) monthTotals[Number(m) - 1] += count;
          });
          days = MONTH_CHIPS.slice(1).map((label, i) => ({ date: label, value: monthTotals[i] }));
        } else if (year !== null && month !== null) {
          // specific month selected — bucket by day
          const daysInMonth = new Date(year, month, 0).getDate();
          days = Array.from({ length: daysInMonth }, (_, i) => {
            const dayNum = i + 1;
            const key = `${year}-${String(month).padStart(2, "0")}-${String(dayNum).padStart(2, "0")}`;
            return { date: String(dayNum), value: dayMap[key] ?? 0 };
          });
        } else {
          // no filter — last 14 days
          const today = new Date();
          days = [];
          for (let i = 13; i >= 0; i--) {
            const d = new Date(today);
            d.setDate(d.getDate() - i);
            const key = d.toISOString().slice(0, 10);
            days.push({ date: key.slice(5), value: dayMap[key] ?? 0 });
          }
        }
        setTimeData(days);
      }
    } catch (e) {
      console.error("fetchCharts:", e);
    } finally {
      setChartLoad(false);
    }
  }, [year, month]);

  useEffect(() => { fetchCharts(); }, [fetchCharts]);
  useEffect(() => { fetchLeads(); setSelected([]); }, [fetchLeads]);

  // ── Post-scrape "send emails?" dialog state ─────────────────────────────
  const [postScrapeOpen, setPostScrapeOpen]   = useState(false);
  const [postScrapeJob,  setPostScrapeJob]    = useState(null); // { id, niche, city, total_scraped, status }
  const [postScrapeMode, setPostScrapeMode]   = useState("batch"); // batch | hot | custom | skip
  const [postScrapeMinScore, setPostScrapeMinScore] = useState(40);
  const [sendingEmails,  setSendingEmails]    = useState(false);
  const [scrapeToast,    setScrapeToast]      = useState(null); // short success/failure banner text

  // Live progress while the chained Audit → Verify → Send pipeline runs
  const [pipelineOpen,   setPipelineOpen]     = useState(false);
  const [pipelineStage,  setPipelineStage]    = useState(null); // auditing | verifying_emails | sending_emails | outreach_done | outreach_failed

  // Polls GET /scraping-jobs every 3s until this job's status is no longer
  // "running" (or we give up after ~3 minutes). Only then do we know the
  // scrape actually finished — the old code just guessed with a 3s timeout.
  const pollScrapeJob = useCallback((jobId, niche, city, attempt = 0) => {
    const MAX_ATTEMPTS = 60; // 60 * 3s = 3 minutes
    fetch(`${API}/scraping-jobs`)
      .then((r) => (r.ok ? r.json() : []))
      .then((jobs) => {
        const job = jobs.find((j) => j.id === jobId);

        if (job && job.status === "done") {
          setScraping(false);
          fetchLeads();
          fetchCharts();
          setPostScrapeJob(job);
          setPostScrapeMode(job.total_scraped >= 5 ? "batch" : "hot");
          setPostScrapeOpen(true);
          return;
        }

        if (job && job.status === "failed") {
          setScraping(false);
          setScrapeToast({ type: "error", text: "Scraping failed — check Pipeline tab for details." });
          setTimeout(() => setScrapeToast(null), 5000);
          return;
        }

        if (attempt >= MAX_ATTEMPTS) {
          // Still running after 3 min — don't block the user, just refresh
          // quietly. They can open the dialog manually from Pipeline tab later.
          setScraping(false);
          fetchLeads();
          fetchCharts();
          setScrapeToast({ type: "info", text: "Scraping is taking longer than usual — still running in the background." });
          setTimeout(() => setScrapeToast(null), 6000);
          return;
        }

        setTimeout(() => pollScrapeJob(jobId, niche, city, attempt + 1), 3000);
      })
      .catch(() => {
        // Network hiccup — keep trying, don't give up on one failed poll
        if (attempt < MAX_ATTEMPTS) {
          setTimeout(() => pollScrapeJob(jobId, niche, city, attempt + 1), 3000);
        } else {
          setScraping(false);
        }
      });
  }, [fetchLeads, fetchCharts]);

  const handleScrape = async () => {
    if (!scrapeForm.niche.trim() || !scrapeForm.city.trim()) return;
    setScraping(true);
    const s = getSettings();
    try {
      const res = await fetch(`${API}/pipeline/scrape`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          niche:       scrapeForm.niche.trim(),
          city:        scrapeForm.city.trim(),
          min_rating:  scrapeForm.min_rating,
          min_reviews: scrapeForm.min_reviews,
          skip_crawl:  false,
          max_leads:   s.maxLeads   ?? 50,     // Settings → Scraper → Max Leads Per Scrape
          auto_audit:  s.autoAudit ?? false,   // Settings → Scraper → Auto-audit new leads
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setScraperOpen(false);
      const usedNiche = scrapeForm.niche.trim(), usedCity = scrapeForm.city.trim();
      setScrapeForm({ niche: s.defaultNiche || "", city: s.defaultCity || "", min_rating: 3.5, min_reviews: 10 });

      if (data.job_id) {
        // Real completion tracking — opens the post-scrape dialog exactly
        // when the job finishes, instead of guessing with a fixed delay.
        pollScrapeJob(data.job_id, usedNiche, usedCity);
      } else {
        // Fallback for older backend responses without job_id
        setTimeout(() => { setScraping(false); fetchLeads(); fetchCharts(); }, 3000);
      }

      // Settings → Export → "Auto-export after scrape" (unrelated to the
      // email dialog — keeps working exactly as before)
      if (s.autoExport) {
        setTimeout(() => handleExport({ niche: usedNiche, city: usedCity }), 3000);
      }
    } catch (e) {
      console.error("scrape:", e);
      setScraping(false);
    }
  };

  // Polls GET /scraping-jobs every 2s and updates the live stage label
  // while the chained audit → verify → send pipeline runs in the background.
  const pollPipelineStage = useCallback((jobId, attempt = 0) => {
    const MAX_ATTEMPTS = 150; // 150 * 2s = 5 minutes — verifying/sending can take a while for big batches
    fetch(`${API}/scraping-jobs`)
      .then((r) => (r.ok ? r.json() : []))
      .then((jobs) => {
        const job = jobs.find((j) => j.id === jobId);
        if (!job) return;

        if (job.status === "outreach_done") {
          setPipelineStage("outreach_done");
          setTimeout(() => {
            setPipelineOpen(false);
            fetchLeads();
            fetchCharts();
            setScrapeToast({ type: "success", text: "All done — leads audited, verified, and emailed." });
            setTimeout(() => setScrapeToast(null), 5000);
          }, 1200); // brief pause so the user sees the final checkmark before it closes
          return;
        }

        if (job.status === "outreach_failed") {
          setPipelineStage("outreach_failed");
          return;
        }

        setPipelineStage(job.status); // "auditing" | "verifying_emails" | "sending_emails" | (still "done" briefly at the start)

        if (attempt >= MAX_ATTEMPTS) {
          setPipelineOpen(false);
          setScrapeToast({ type: "info", text: "Still running in the background — check the Pipeline tab for progress." });
          setTimeout(() => setScrapeToast(null), 6000);
          return;
        }
        setTimeout(() => pollPipelineStage(jobId, attempt + 1), 2000);
      })
      .catch(() => {
        if (attempt < MAX_ATTEMPTS) setTimeout(() => pollPipelineStage(jobId, attempt + 1), 2000);
      });
  }, [fetchLeads, fetchCharts]);

  // Confirm button on the post-scrape dialog — chains Audit → Verify → Send
  // as one background pipeline, with a live progress animation, then closes.
  const handlePostScrapeConfirm = async () => {
    if (!postScrapeJob) return;

    if (postScrapeMode === "skip") {
      setPostScrapeOpen(false);
      return;
    }

    const minScore =
      postScrapeMode === "batch" ? 0 :
      postScrapeMode === "hot"   ? 85 :
      postScrapeMinScore;

    setSendingEmails(true);
    try {
      const res = await fetch(`${API}/pipeline/post-scrape-auto`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id:    postScrapeJob.id,
          niche:     postScrapeJob.niche,
          city:      postScrapeJob.city,
          min_score: minScore,
          dry_run:   false,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      setPostScrapeOpen(false);
      setPipelineStage("auditing"); // show something immediately, poller will correct it within 2s
      setPipelineOpen(true);
      pollPipelineStage(postScrapeJob.id);
    } catch (e) {
      console.error("post-scrape pipeline:", e);
      setScrapeToast({ type: "error", text: "Couldn't start the pipeline — check backend logs." });
      setTimeout(() => setScrapeToast(null), 5000);
    } finally {
      setSendingEmails(false);
    }
  };

  const handleExport = async (override = {}) => {
    const s = getSettings();
    const format = s.exportFormat || "csv";
    const activeNiche = override.niche ?? niche;
    const activeCity  = override.city  ?? city;
    const params = new URLSearchParams({
      ...(activeNiche && { niche: activeNiche }),
      ...(activeCity  && { city:  activeCity  }),
    });

    if (format === "csv") {
      window.open(`${API}/leads/export/csv?${params}`, "_blank");
      return;
    }

    // JSON / Excel: pull the real (filtered) lead rows and build the file
    // client-side — the backend only has a CSV exporter today.
    try {
      const res  = await fetch(`${API}/leads?${params}&limit=9999`);
      const data = await res.json();
      const rows = data.leads ?? [];

      if (format === "json") {
        const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
        const url  = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `leads_${activeNiche || "all"}_${activeCity || "all"}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } else if (format === "excel") {
        // Lightweight, dependency-free "Excel" export: an HTML table saved
        // with an .xls extension — Excel opens this natively with real data,
        // no xlsx library needed on the frontend.
        const cols = ["name", "niche", "city", "phone", "email", "website", "final_score", "tier", "pipeline_stage"];
        const headerRow = `<tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
        const bodyRows = rows.map(
          (r) => `<tr>${cols.map((c) => `<td>${(r[c] ?? "").toString().replace(/</g, "&lt;")}</td>`).join("")}</tr>`
        ).join("");
        const html = `<table>${headerRow}${bodyRows}</table>`;
        const blob = new Blob([html], { type: "application/vnd.ms-excel" });
        const url  = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `leads_${activeNiche || "all"}_${activeCity || "all"}.xls`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      console.error("export:", e);
    }
  };

  const toggleAll = () =>
    setSelected(selected.length === leads.length ? [] : leads.map((l) => l.id));
  const toggleOne = (id) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="leads-page">

      {/* ── month/year filter ── */}
      <div className="leads-toolbar">
        <div className="leads-toolbar-label">Filter by period</div>
        <MonthYearFilter year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); setPage(1); }} />
      </div>

      {/* ── stat cards ── */}
      <div className="stat-grid">
        <StatCard icon="ti-users"          color="#e8622a" bg="rgba(232,98,42,0.1)"   label="Total Leads"      value={totalLeads} sub="All scraped businesses"   loading={chartLoad} />
        <StatCard icon="ti-flame"          color="#dc2626" bg="rgba(220,38,38,0.08)"  label="Hot Leads"        value={hotCount}   sub="Score 85+"               loading={chartLoad} />
        <StatCard icon="ti-mail-check"     color="#16a34a" bg="rgba(22,163,74,0.1)"   label="Emails Verified"  value={verified}   sub="Safe to send"            loading={chartLoad} />
        <StatCard icon="ti-building-store" color="#2563eb" bg="rgba(37,99,235,0.1)"   label="Onboarded"        value={onboarded}  sub="Joined ecomdropship.ai"  loading={chartLoad} />
      </div>

      {/* ── charts row ── */}
      <div className="charts-grid">

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Leads by niche</div>
              <div className="panel-sub">Top categories in your pipeline</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? (
              <div className="chart-skeleton" />
            ) : nicheData.length === 0 ? (
              <div style={{ color: "rgba(255,255,255,0.2)", fontSize: 12, textAlign: "center", paddingTop: 40 }}>
                No data yet — run scraper first
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={150}>
                <BarChart
                  data={nicheData}
                  layout="vertical"
                  margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
                >
                  <XAxis type="number" hide />
                  <YAxis
                    dataKey="name" type="category" width={110}
                    tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false}
                  />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ fill: "rgba(0,0,0,0.04)" }} />
                  <Bar dataKey="value" radius={[0, 5, 5, 0]} barSize={14}>
                    {nicheData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Leads by tier</div>
              <div className="panel-sub">Score distribution</div>
            </div>
          </div>
          <div className="chart-body donut-body">
            {chartLoad ? (
              <div className="chart-skeleton" />
            ) : tierData.every((t) => t.value === 0) ? (
              <div style={{ color: "rgba(255,255,255,0.2)", fontSize: 12, textAlign: "center", paddingTop: 40 }}>
                No scored leads yet
              </div>
            ) : (
              <>
                <ResponsiveContainer width={130} height={130}>
                  <PieChart>
                    <Pie
                      data={tierData.filter((t) => t.value > 0)}
                      cx="50%" cy="50%"
                      innerRadius={38} outerRadius={58}
                      paddingAngle={3} dataKey="value"
                      animationBegin={0} animationDuration={900}
                    >
                      {tierData.filter((t) => t.value > 0).map((entry, i) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, n) => [v.toLocaleString(), n]} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-legend">
                  {tierData.map((t, i) => (
                    <div key={i} className="dl-item">
                      <span className="dl-dot" style={{ background: t.color }} />
                      <span className="dl-name">{t.name}</span>
                      <span className="dl-val">{t.value.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Outreach funnel</div>
              <div className="panel-sub">Campaign progress</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? (
              <div className="chart-skeleton" />
            ) : (
              <div className="funnel-list">
                {funnelData.map((row, i) => {
                  const max = funnelData[0]?.value || 1;
                  const pct = Math.round((row.value / max) * 100);
                  return (
                    <div key={i} className="funnel-row">
                      <span className="funnel-label">{row.name}</span>
                      <div className="funnel-track">
                        <div className="funnel-fill" style={{ width: `${pct}%`, background: row.fill }}>
                          {pct > 12 && <span>{row.value.toLocaleString()}</span>}
                        </div>
                      </div>
                      <span className="funnel-num">{row.value.toLocaleString()}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Leads over time</div>
              <div className="panel-sub">
                {year === null
                  ? "Scraped in the last 14 days"
                  : month === null
                  ? `Scraped in ${year}`
                  : `Scraped in ${MONTH_CHIPS[month]} ${year}`}
              </div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? (
              <div className="chart-skeleton" />
            ) : timeData.every((d) => d.value === 0) ? (
              <div style={{ color: "rgba(255,255,255,0.2)", fontSize: 12, textAlign: "center", paddingTop: 40 }}>
                No data yet — run scraper first
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={150}>
                <AreaChart data={timeData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="leadsOverTimeFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%"  stopColor="#e8622a" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#e8622a" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false} />
                  <YAxis hide allowDecimals={false} />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ stroke: "rgba(255,255,255,0.1)" }} />
                  <Area
                    type="monotone" dataKey="value"
                    stroke="#e8622a" strokeWidth={2}
                    fill="url(#leadsOverTimeFill)"
                    animationDuration={900}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* ── table panel ── */}
      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">All leads</div>
            <div className="panel-sub">Sorted by score — highest first</div>
          </div>
          <div className="table-actions">
            <select
              className="filter-select"
              value={tier}
              onChange={(e) => { setTier(e.target.value); setPage(1); }}
            >
              <option value="">All tiers</option>
              <option value="hot">🔥 Hot</option>
              <option value="warm">Warm</option>
              <option value="cold">Cold</option>
            </select>

            {/* ── niche searchable dropdown ── */}
            <NicheSelect
              value={niche}
              onChange={(val) => { setNiche(val); setPage(1); }}
            />

            {/* ── city filter with autocomplete ── */}
            <CitySearch
              value={city}
              onChange={(val) => { setCity(val); setPage(1); }}
              placeholder="City..."
            />

            {/* Feature 6 — "Stuck Leads": quick filter for leads with
                genuinely no outreach path (no email + no phone, or DNC) */}
            <button
              className="btn-ghost"
              onClick={() => { setStuckOnly((v) => !v); setPage(1); }}
              title="Leads with no email AND no phone (or on the Do-Not-Contact list) — no outreach is possible for them"
              style={stuckOnly ? {
                background: "rgba(232,98,42,0.16)", color: "#e8622a", borderColor: "#e8622a",
              } : undefined}
            >
              <i className="ti ti-plug-connected-x" /> <span>Stuck leads</span>
            </button>

            <button className="btn-ghost" onClick={() => handleExport()}>
              <i className="ti ti-download" /> <span>Export</span>
            </button>
            <button className="btn-primary" onClick={() => setScraperOpen(true)}>
              <i className="ti ti-plus" /> <span>Scrape new</span>
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={selected.length === leads.length && leads.length > 0}
                    onChange={toggleAll}
                    style={{ accentColor: "#e8622a" }}
                  />
                </th>
                <th>Business</th>
                <th>City / Niche</th>
                <th>Score</th>
                <th>Tier</th>
                <th>Email</th>
                <th>Decision Maker</th>
                <th>Stage</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="skeleton-row">
                    {Array.from({ length: 9 }).map((_, j) => (
                      <td key={j}><div className="skeleton" /></td>
                    ))}
                  </tr>
                ))
              ) : leads.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty-state">
                    <i className={stuckOnly ? "ti ti-circle-check" : "ti ti-users-off"} />
                    <p>{stuckOnly ? "No stuck leads 🎉" : "No leads found"}</p>
                    <span>{stuckOnly
                      ? "Every lead has at least one outreach path available right now."
                      : "Try adjusting your filters or scrape new leads"}</span>
                  </td>
                </tr>
              ) : (
                leads.map((lead) => {
                  const eb = emailBadge(lead.email_status);
                  return (
                    <tr key={lead.id} className={selected.includes(lead.id) ? "row-selected" : ""}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.includes(lead.id)}
                          onChange={() => toggleOne(lead.id)}
                          style={{ accentColor: "#e8622a" }}
                        />
                      </td>
                      <td>
                        <div className="biz-name">{lead.name ?? "—"}</div>
                        <div className="biz-src">
                          <span className="src-dot" style={{ background: srcColor(lead.source) }} />
                          {srcLabel(lead.source)}
                          {lead.website && (
                            <a href={lead.website} target="_blank" rel="noreferrer" className="biz-link" onClick={(e) => e.stopPropagation()}>
                              <i className="ti ti-external-link" />
                            </a>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="city-text">{lead.city ?? "—"}</div>
                        <div className="niche-text">{lead.niche ?? "—"}</div>
                      </td>
                      <td>
                        <ScoreBar
                          score={lead.final_score}
                          incomplete={lead.score_incomplete}
                          incompleteReason={lead.score_incomplete_reason}
                        />
                      </td>
                      <td>
                        <span className={`tier-badge ${tierClass(lead.tier)}`}>
                          {tierLabel(lead.tier)}
                        </span>
                      </td>
                      <td>
                        <span className={`email-badge ${eb.cls}`}>{eb.label}</span>
                      </td>
                      <td>
                        {lead.linkedin_found && lead.linkedin_name ? (
                          <>
                            <div className="dm-name">{lead.linkedin_name}</div>
                            {lead.linkedin_title && <div className="dm-title">{lead.linkedin_title}</div>}
                          </>
                        ) : (
                          <span className="dm-none">Not found</span>
                        )}
                      </td>
                      <td>
                        <span className="stage-badge">{stageLabel(lead.pipeline_stage)}</span>
                        {lead.outreach_reason && (
                          <i
                            className="ti ti-info-circle outreach-reason-icon"
                            title={lead.outreach_reason}
                          />
                        )}
                      </td>
                      <td>
                        <div className="row-actions">
                          {lead.tier === "hot" && !lead.do_not_contact && (
                            <button className="btn-primary btn-xs" title="Call this lead" onClick={() => navigate("/admin/calls")}>
                              <i className="ti ti-phone" />
                            </button>
                          )}
                          {lead.email_verified && !lead.do_not_contact && (
                            <button className="btn-ghost btn-xs" title="Email this lead" onClick={() => navigate("/admin/email")}>
                              <i className="ti ti-mail" />
                            </button>
                          )}
                          <button className="btn-ghost btn-xs" title="View lead" onClick={() => setViewLeadId(lead.id)}>
                            <i className="ti ti-eye" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="pagination">
          <div className="pag-info">
            {total === 0
              ? "No leads"
              : `Showing ${Math.min((page - 1) * LIMIT + 1, total)}–${Math.min(page * LIMIT, total)} of ${total.toLocaleString()} leads`}
          </div>
          <div className="pag-buttons">
            <button className="pag-btn" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>‹</button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const p =
                totalPages <= 5 ? i + 1
                : page <= 3 ? i + 1
                : page >= totalPages - 2 ? totalPages - 4 + i
                : page - 2 + i;
              return (
                <button key={p} className={`pag-btn ${p === page ? "active" : ""}`} onClick={() => setPage(p)}>
                  {p}
                </button>
              );
            })}
            <button className="pag-btn" disabled={page === totalPages} onClick={() => setPage((p) => p + 1)}>›</button>
          </div>
        </div>
      </div>

      {/* ── scraper modal ── */}
      {scraperOpen && (
        <div className="modal-overlay" onClick={() => setScraperOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Scrape new leads</div>
                <div className="modal-sub">Pulls from Google Maps, Yelp &amp; SerpAPI</div>
              </div>
              <button className="modal-close" onClick={() => setScraperOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>

            <div className="modal-body">
              <div className="form-row">
                <div className="form-group">
                  <label>Niche *</label>
                  {/* ── niche searchable dropdown in modal ── */}
                  <NicheSelect
                    value={scrapeForm.niche}
                    onChange={(val) => setScrapeForm((f) => ({ ...f, niche: val }))}
                    placeholder="Select niche..."
                  />
                </div>
                <div className="form-group">
                  <label>City *</label>
                  <CitySearch
                    value={scrapeForm.city}
                    onChange={(val) => setScrapeForm((f) => ({ ...f, city: val }))}
                    placeholder="e.g. Miami, New York..."
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Min rating</label>
                  <input
                    type="number" min="0" max="5" step="0.5"
                    value={scrapeForm.min_rating}
                    onChange={(e) => setScrapeForm((f) => ({ ...f, min_rating: parseFloat(e.target.value) || 0 }))}
                  />
                </div>
                <div className="form-group">
                  <label>Min reviews</label>
                  <input
                    type="number" min="0"
                    value={scrapeForm.min_reviews}
                    onChange={(e) => setScrapeForm((f) => ({ ...f, min_reviews: parseInt(e.target.value) || 0 }))}
                  />
                </div>
              </div>

              <div style={{
                background: "rgba(232,98,42,0.06)", border: "1px solid rgba(232,98,42,0.15)",
                borderRadius: 10, padding: "10px 14px", fontSize: 11.5,
                color: "rgba(255,255,255,0.4)", display: "flex", gap: 8, alignItems: "flex-start",
              }}>
                <i className="ti ti-info-circle" style={{ color: "#e8622a", marginTop: 1, flexShrink: 0 }} />
                <span>
                  Scraping runs in the background. The page will refresh automatically in a few
                  seconds to show newly found leads.
                </span>
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setScraperOpen(false)}>Cancel</button>
              <button
                className="btn-primary"
                onClick={handleScrape}
                disabled={scraping || !scrapeForm.niche.trim() || !scrapeForm.city.trim()}
              >
                {scraping ? (
                  <><i className="ti ti-loader-2 spin" /> Scraping…</>
                ) : (
                  <><i className="ti ti-search" /> Start scraping</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {pipelineOpen && (
        <div className="modal-overlay">
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Running outreach pipeline…</div>
                <div className="modal-sub">
                  {postScrapeJob?.niche} · {postScrapeJob?.city}
                </div>
              </div>
            </div>

            <div className="modal-body">
              {[
                { key: "auditing",         icon: "ti-target-arrow", label: "Scoring leads (audit)" },
                { key: "verifying_emails", icon: "ti-mail-check",   label: "Verifying email addresses" },
                { key: "sending_emails",   icon: "ti-send",         label: "Sending outreach emails" },
              ].map((step, i) => {
                const order = ["auditing", "verifying_emails", "sending_emails", "outreach_done"];
                const currentIdx = order.indexOf(pipelineStage);
                const stepIdx = order.indexOf(step.key);
                const isDone    = pipelineStage === "outreach_done" || currentIdx > stepIdx;
                const isActive  = currentIdx === stepIdx && pipelineStage !== "outreach_failed";
                const isFailed  = pipelineStage === "outreach_failed" && currentIdx <= stepIdx;

                return (
                  <div key={step.key} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 4px", opacity: isDone || isActive ? 1 : 0.4,
                  }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      background: isDone ? "#16a34a" : isFailed ? "#dc2626" : isActive ? "#e8622a" : "rgba(255,255,255,0.08)",
                      color: "#fff",
                    }}>
                      {isDone ? (
                        <i className="ti ti-check" style={{ fontSize: 15 }} />
                      ) : isActive ? (
                        <i className="ti ti-loader-2 spin" style={{ fontSize: 15 }} />
                      ) : (
                        <i className={`ti ${step.icon}`} style={{ fontSize: 13 }} />
                      )}
                    </div>
                    <span style={{ fontSize: 13.5, fontWeight: isActive ? 600 : 500 }}>
                      {step.label}{isActive ? "…" : ""}
                    </span>
                  </div>
                );
              })}

              {pipelineStage === "outreach_done" && (
                <div style={{
                  marginTop: 10, display: "flex", alignItems: "center", gap: 8,
                  color: "#16a34a", fontSize: 13, fontWeight: 600,
                }}>
                  <i className="ti ti-circle-check" /> Done — emails sent successfully.
                </div>
              )}

              {pipelineStage === "outreach_failed" && (
                <div style={{
                  marginTop: 10, display: "flex", alignItems: "center", gap: 8,
                  color: "#dc2626", fontSize: 13, fontWeight: 600,
                }}>
                  <i className="ti ti-alert-circle" /> Something went wrong — check backend logs.
                </div>
              )}

              {pipelineStage === "outreach_failed" && (
                <div className="modal-footer" style={{ marginTop: 12, padding: 0 }}>
                  <button className="btn-ghost" onClick={() => setPipelineOpen(false)}>Close</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {postScrapeOpen && postScrapeJob && (
        <div className="modal-overlay" onClick={() => setPostScrapeOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Scrape complete — send emails?</div>
                <div className="modal-sub">
                  {postScrapeJob.total_scraped ?? 0} new lead{(postScrapeJob.total_scraped ?? 0) === 1 ? "" : "s"} added for{" "}
                  <b>{postScrapeJob.niche}</b> in <b>{postScrapeJob.city}</b>
                </div>
              </div>
              <button className="modal-close" onClick={() => setPostScrapeOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>

            <div className="modal-body">
              {postScrapeJob.summary && (
                <div style={{
                  display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap",
                }}>
                  <div style={{
                    flex: "1 1 100px", background: "rgba(255,255,255,0.04)", borderRadius: 10,
                    padding: "10px 12px", textAlign: "center",
                  }}>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{postScrapeJob.summary.total_found}</div>
                    <div style={{ fontSize: 10.5, color: "#999" }}>Mile (found)</div>
                  </div>
                  <div style={{
                    flex: "1 1 100px", background: "rgba(46,204,113,0.08)", borderRadius: 10,
                    padding: "10px 12px", textAlign: "center",
                  }}>
                    <div style={{ fontSize: 20, fontWeight: 700, color: "#2ecc71" }}>{postScrapeJob.summary.total_created}</div>
                    <div style={{ fontSize: 10.5, color: "#999" }}>Ban gayin (leads)</div>
                  </div>
                  <div style={{
                    flex: "1 1 100px", background: "rgba(232,98,42,0.08)", borderRadius: 10,
                    padding: "10px 12px", textAlign: "center",
                  }}>
                    <div style={{ fontSize: 20, fontWeight: 700, color: "#e8622a" }}>{postScrapeJob.summary.total_skipped}</div>
                    <div style={{ fontSize: 10.5, color: "#999" }}>Skip hui</div>
                  </div>
                </div>
              )}

              {postScrapeJob.summary && postScrapeJob.summary.total_skipped > 0 && (
                <div style={{
                  fontSize: 11, color: "#999", marginBottom: 14, display: "flex",
                  flexDirection: "column", gap: 3,
                }}>
                  {postScrapeJob.summary.skip_reasons.low_rating_or_reviews > 0 && (
                    <div>• {postScrapeJob.summary.skip_reasons.low_rating_or_reviews} — rating/reviews threshold se kam</div>
                  )}
                  {postScrapeJob.summary.skip_reasons.duplicate_in_batch > 0 && (
                    <div>• {postScrapeJob.summary.skip_reasons.duplicate_in_batch} — isi scrape mein duplicate</div>
                  )}
                  {postScrapeJob.summary.skip_reasons.already_in_database > 0 && (
                    <div>• {postScrapeJob.summary.skip_reasons.already_in_database} — pehle se database mein maujood</div>
                  )}
                  {postScrapeJob.summary.skip_reasons.over_max_leads_cap > 0 && (
                    <div>• {postScrapeJob.summary.skip_reasons.over_max_leads_cap} — "Max Leads Per Scrape" limit se zyada</div>
                  )}
                </div>
              )}

              <div className="form-group" style={{ gap: 10, display: "flex", flexDirection: "column" }}>
                {[
                  { key: "batch", icon: "ti-database",     label: "This batch — all leads just scraped",
                    sub: `Every lead from this scrape (${postScrapeJob.total_scraped ?? 0} leads), regardless of score` },
                  { key: "hot",   icon: "ti-flame",         label: "Hot leads only",
                    sub: "Score ≥ 85 — highest-quality matches from this batch" },
                  { key: "custom", icon: "ti-adjustments",  label: "Custom score threshold",
                    sub: "Pick your own minimum score" },
                  { key: "skip",  icon: "ti-player-skip-forward", label: "Skip for now",
                    sub: "I'll review and send manually later" },
                ].map((opt) => (
                  <label
                    key={opt.key}
                    style={{
                      display: "flex", alignItems: "flex-start", gap: 10,
                      padding: "10px 12px", borderRadius: 10, cursor: "pointer",
                      border: postScrapeMode === opt.key ? "1px solid #e8622a" : "1px solid #e8e8e8",
                      background: postScrapeMode === opt.key ? "rgba(232,98,42,0.06)" : "transparent",
                    }}
                  >
                    <input
                      type="radio" name="postScrapeMode" value={opt.key}
                      checked={postScrapeMode === opt.key}
                      onChange={() => setPostScrapeMode(opt.key)}
                      style={{ marginTop: 3 }}
                    />
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13.5, fontWeight: 600 }}>
                        <i className={`ti ${opt.icon}`} style={{ color: "#e8622a" }} /> {opt.label}
                      </div>
                      <div style={{ fontSize: 11.5, color: "#999", marginTop: 2 }}>{opt.sub}</div>
                    </div>
                  </label>
                ))}

                {postScrapeMode === "custom" && (
                  <div className="form-group" style={{ marginTop: 4 }}>
                    <label>Minimum score</label>
                    <input
                      type="number" min="0" max="145"
                      value={postScrapeMinScore}
                      onChange={(e) => setPostScrapeMinScore(parseInt(e.target.value) || 0)}
                    />
                  </div>
                )}
              </div>

              <div style={{
                background: "rgba(232,98,42,0.06)", border: "1px solid rgba(232,98,42,0.15)",
                borderRadius: 10, padding: "10px 14px", fontSize: 11.5,
                color: "rgba(255,255,255,0.4)", display: "flex", gap: 8, alignItems: "flex-start", marginTop: 12,
              }}>
                <i className="ti ti-info-circle" style={{ color: "#e8622a", marginTop: 1, flexShrink: 0 }} />
                <span>
                  Only leads with a verified email address will actually receive one. If no reply comes back
                  in a few days, the system automatically follows up by SMS, then by call.
                </span>
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setPostScrapeOpen(false)}>Close</button>
              <button
                className="btn-primary"
                onClick={handlePostScrapeConfirm}
                disabled={sendingEmails}
              >
                {sendingEmails ? (
                  <><i className="ti ti-loader-2 spin" /> Starting…</>
                ) : postScrapeMode === "skip" ? (
                  <><i className="ti ti-x" /> Skip</>
                ) : (
                  <><i className="ti ti-mail" /> Send emails</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {scrapeToast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 9999,
          background: scrapeToast.type === "error" ? "#dc2626" : scrapeToast.type === "info" ? "#3b82f6" : "#16a34a",
          color: "#fff", padding: "12px 18px", borderRadius: 10, fontSize: 13,
          boxShadow: "0 8px 24px rgba(0,0,0,0.25)", display: "flex", alignItems: "center", gap: 8,
          maxWidth: 340,
        }}>
          <i className={`ti ${scrapeToast.type === "error" ? "ti-alert-circle" : scrapeToast.type === "info" ? "ti-info-circle" : "ti-check"}`} />
          {scrapeToast.text}
        </div>
      )}

      <LeadDetailModal leadId={viewLeadId} onClose={() => setViewLeadId(null)} />
    </div>
  );
}