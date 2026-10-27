// PublicSite.jsx — Public Landing Page for Suppliers
// Theme: Ecomdropship-matched dark theme with orange accents
// Fonts: Inter + Syne (same as ecomdropship)

import { useState, useEffect, useRef } from "react";
import "./PublicSite.css";

// ── Counter animation hook ─────────────────────────────────────────────────
function useCountUp(target, duration = 1200) {
  const [val, setVal] = useState(0);
  const startRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    startRef.current = null;
    const step = (ts) => {
      if (!startRef.current) startRef.current = ts;
      const elapsed = ts - startRef.current;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setVal(Math.round(target * eased));
      if (progress < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);

  return val;
}
// Sparkline chart component
function SparklineChart({ monthly, dropship }) {
  const pts = Array.from({ length: 12 }, (_, i) => {
    const v = monthly + (dropship - monthly) * (i / 11) * 1.1;
    return v;
  });
  const mn = Math.min(...pts), mx = Math.max(...pts);
  const coords = pts.map((v, i) => {
    const x = i * (80 / 11);
    const y = 36 - ((v - mn) / (mx - mn || 1)) * 30;
    return `${x},${y}`;
  }).join(" ");
  const lastPt = coords.split(" ").pop().split(",");
  return (
    <svg width="80" height="40" viewBox="0 0 80 40">
      <polyline points={coords} fill="none" stroke="#e8622a" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastPt[0]} cy={lastPt[1]} r="3" fill="#e8622a" />
    </svg>
  );
}

// Donut chart component
function DonutChart({ pct }) {
  const r = 21, circ = 2 * Math.PI * r;
  const offset = circ * (1 - Math.min(pct / 100, 1));
  return (
    <svg width="54" height="54" viewBox="0 0 54 54">
      <circle cx="27" cy="27" r={r} fill="none" stroke="#f0efed" strokeWidth="7" />
      <circle cx="27" cy="27" r={r} fill="none" stroke="var(--orange)" strokeWidth="7"
        strokeLinecap="round" strokeDasharray={circ.toFixed(1)}
        strokeDashoffset={offset.toFixed(1)} transform="rotate(-90 27 27)"
        style={{ transition: "stroke-dashoffset 0.5s cubic-bezier(0.22,1,0.36,1)" }} />
      <text x="27" y="31" textAnchor="middle" fontFamily="Syne,sans-serif"
        fontSize="10" fontWeight="900" fill="var(--orange)">
        {pct}%
      </text>
    </svg>
  );
}
export default function PublicSite() {

  // ── Calculator State ──────────────────────────────────────────────────────
  const [products, setProducts]        = useState(10);
  const [unitsSold, setUnitsSold]      = useState(50);
  const [wholesalePrice, setWholesale] = useState(4);
  const [retailPrice, setRetail]       = useState(8);
  const [commission, setCommission]    = useState(15);
  const [months, setMonths]            = useState(12);

  // ── Calculator Logic ──────────────────────────────────────────────────────
  const totalUnits        = products * unitsSold;
  const monthlyWholesale  = totalUnits * wholesalePrice;
  const monthlyDropship   = totalUnits * retailPrice * (1 - commission / 100);
  const extraPerMonth     = monthlyDropship - monthlyWholesale;
  const totalUplift       = extraPerMonth * months;
  const upliftPct         = monthlyWholesale > 0
    ? Math.round((extraPerMonth / monthlyWholesale) * 100)
    : 0;

  const fmt = (n) => "$" + Math.abs(Math.round(n)).toLocaleString("en-US");

  const animatedExtra     = useCountUp(Math.round(extraPerMonth), 800);
  const animatedUplift    = useCountUp(Math.round(totalUplift), 800);
  const animatedDropship  = useCountUp(Math.round(monthlyDropship), 800);
  const animatedWholesale = useCountUp(Math.round(monthlyWholesale), 600);

  // ── Scroll reveal ─────────────────────────────────────────────────────────
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) e.target.classList.add("visible");
      }),
      { threshold: 0.12 }
    );
    document.querySelectorAll(".reveal").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  // ── Navbar scroll ─────────────────────────────────────────────────────────
  useEffect(() => {
    const nav = document.querySelector(".ps-nav");
    const onScroll = () => nav?.classList.toggle("scrolled", window.scrollY > 20);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // ── Loader ────────────────────────────────────────────────────────────────
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let p = 0;
    const interval = setInterval(() => {
      p += Math.random() * 18 + 5;
      if (p >= 100) {
        p = 100;
        clearInterval(interval);
        setTimeout(() => setLoaded(true), 400);
      }
      setProgress(Math.min(p, 100));
    }, 60);
    return () => clearInterval(interval);
  }, []);

  // ── Mobile nav ────────────────────────────────────────────────────────────
  const [navOpen, setNavOpen] = useState(false);
  const closeNav = () => setNavOpen(false);

  return (
    <div className="ps-root">

      {/* ── LOADER ── */}
      {!loaded && (
        <div className="ps-loader">
          <div className="ps-loader-logo">
            ECOM<span>DROP</span>.AI
          </div>
          <div className="ps-loader-bar-wrap">
            <div className="ps-loader-bar" style={{ width: `${progress}%` }} />
          </div>
          <div className="ps-loader-num">{Math.round(progress)}%</div>
        </div>
      )}

      {/* ── NAVBAR ── */}
      <nav className="ps-nav">
        <div className="ps-nav-inner">

          <div className="ps-logo">
            ECOM<span>DROP</span>.AI
          </div>

          <ul className={`ps-nav-links ${navOpen ? "open" : ""}`}>
            <li><a href="#how-it-works" onClick={closeNav}>How It Works</a></li>
            <li><a href="#calculator" onClick={closeNav}>Calculator</a></li>
            <li>
              <a href="#calculator" className="ps-nav-cta" onClick={closeNav}>
                Join Now →
              </a>
            </li>
          </ul>

          <button
            className={`ps-hamburger ${navOpen ? "open" : ""}`}
            onClick={() => setNavOpen(!navOpen)}
            aria-label="Toggle menu"
          >
            <span /><span /><span />
          </button>

        </div>
      </nav>

      {/* ── HERO ── */}
      <section className="ps-hero" id="hero">
        {/* Dark overlay */}
        <div className="ps-hero-overlay" />

        <div className="ps-hero-content">

          <div className="ps-hero-badge">
            <span className="ps-badge-dot" />
            Now Onboarding Wholesale Suppliers
          </div>

          <h1 className="ps-hero-heading">
            Double Your Revenue.<br />
            <span className="ps-orange">Without Extra Work.</span>
          </h1>

          <p className="ps-hero-sub">
            You make the products. We sell them at 2× your wholesale price.
            Selling at <strong>$4 wholesale?</strong> Earn{" "}
            <strong className="ps-orange">$8 per unit</strong> on our dropship platform.
          </p>

          <div className="ps-hero-actions">
            <a href="#calculator" className="ps-btn-primary">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
              Calculate My Revenue
            </a>
            <a href="#how-it-works" className="ps-btn-ghost">
              See How It Works
            </a>
          </div>

          <div className="ps-trust">
            <div className="ps-trust-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
              No upfront cost
            </div>
            <div className="ps-trust-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
              We handle marketing
            </div>
            <div className="ps-trust-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
              You just fulfill orders
            </div>
          </div>

          <div className="ps-scroll-hint">
            <span>Scroll</span>
            <div className="ps-scroll-arrow" />
          </div>

        </div>
      </section>

      {/* ── STATS BAR ── */}
      <div className="ps-stats-bar">
        <div className="ps-stats-bar-inner">
          <div className="ps-stat-item reveal">
            <div className="ps-stat-num">2×</div>
            <div className="ps-stat-lbl">Revenue Multiplier</div>
          </div>
          <div className="ps-stat-item reveal reveal-delay-1">
            <div className="ps-stat-num">7 Days</div>
            <div className="ps-stat-lbl">Payment Cycle</div>
          </div>
          <div className="ps-stat-item reveal reveal-delay-2">
            <div className="ps-stat-num">24 hrs</div>
            <div className="ps-stat-lbl">Listing Turnaround</div>
          </div>
          <div className="ps-stat-item reveal reveal-delay-3">
            <div className="ps-stat-num">$0</div>
            <div className="ps-stat-lbl">Upfront Fees</div>
          </div>
        </div>
      </div>

     {/* ── HOW IT WORKS — Option C: Staggered Offset Cards ── */}
{/* Paste this section into PublicSite.jsx in place of your current how-it-works section */}

<section className="ps-how" id="how-it-works">
  <div className="ps-how-inner">

    <div className="ps-how-header reveal">
      <span className="ps-how-label">Simple Process</span>
      <h2 className="ps-how-heading">
        How It <span className="ps-orange">Works</span>
      </h2>
      <p className="ps-how-sub">
        Three steps. No tech setup. No upfront investment.
      </p>
    </div>

    <div className="ps-how-grid">

      {/* ── CARD 1 ── top position */}
      <div className="ps-how-card ps-how-card--1 reveal">
        <div className="ps-how-card-top">
          <span className="ps-how-step-label">Step 01</span>
          <div className="ps-how-icon-wrap">
            {/* List / Catalog icon */}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
              <rect x="9" y="3" width="6" height="4" rx="1"/>
              <path d="M9 12h6M9 16h4"/>
            </svg>
          </div>
        </div>
        <h3 className="ps-how-card-title">List Your Products</h3>
        <p className="ps-how-card-body">
          Share your product catalog with us. We create professional
          listings on our storefront in under 24 hours.
        </p>
        <div className="ps-how-card-badge">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
          </svg>
          24 hr turnaround
        </div>
      </div>

      {/* ── CARD 2 ── center, highlighted */}
      <div className="ps-how-card ps-how-card--2 ps-how-card--accent reveal reveal-delay-1">
        <div className="ps-how-card-top">
          <span className="ps-how-step-label ps-how-step-label--white">Step 02</span>
          <div className="ps-how-icon-wrap ps-how-icon-wrap--white">
            {/* Shopping bag icon */}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/>
              <line x1="3" y1="6" x2="21" y2="6"/>
              <path d="M16 10a4 4 0 0 1-8 0"/>
            </svg>
          </div>
        </div>
        <h3 className="ps-how-card-title ps-how-card-title--white">We Get You Orders</h3>
        <p className="ps-how-card-body ps-how-card-body--white">
          Our platform handles all marketing, ads, and customer
          acquisition. You receive order notifications with shipping details.
        </p>
        <div className="ps-how-card-badge ps-how-card-badge--dark">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/>
          </svg>
          Zero marketing effort
        </div>
      </div>

      {/* ── CARD 3 ── bottom position */}
      <div className="ps-how-card ps-how-card--3 reveal reveal-delay-2">
        <div className="ps-how-card-top">
          <span className="ps-how-step-label">Step 03</span>
          <div className="ps-how-icon-wrap">
            {/* Wallet / money icon */}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="5" width="20" height="14" rx="2"/>
              <path d="M16 13a1 1 0 1 0 2 0 1 1 0 0 0-2 0"/>
              <path d="M2 10h20"/>
            </svg>
          </div>
        </div>
        <h3 className="ps-how-card-title">Ship &amp; Get Paid</h3>
        <p className="ps-how-card-body">
          Ship directly to the customer. We pay you within 7 days.
          No middlemen. Pure profit at 2× your wholesale rate.
        </p>
        <div className="ps-how-card-badge">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="1" x2="12" y2="23"/>
            <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
          </svg>
          Paid in 7 days
        </div>
      </div>

    </div>
  </div>
</section>
      {/* ── REVENUE CALCULATOR ── */}
<section className="ps-calc-section" id="calculator">
  <div className="ps-section-inner">
    <span className="ps-section-label">Free Tool</span>
    <h2 className="ps-section-heading reveal">
      Revenue <span className="ps-orange">Calculator</span>
    </h2>
    <p className="ps-section-sub reveal">
      Drag the sliders to see exactly how much you could earn by joining EcomDrop.AI
    </p>

    <div className="ps-calc-wrapper reveal">

      {/* LEFT: Inputs */}
      <div className="ps-calc-inputs">
        <div>
          <h3 className="ps-calc-title">Your Numbers</h3>
          <p className="ps-calc-subtitle">Adjust sliders to match your business</p>
        </div>

        {/* Products */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 3l-4 4-4-4"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Number of Products</label>
              <span className="ps-field-val">{products}</span>
            </div>
          </div>
          <input type="range" min="1" max="100" step="1" value={products}
            onChange={(e) => setProducts(Number(e.target.value))} className="ps-slider" />
          <div className="ps-slider-labels"><span>1</span><span>100</span></div>
        </div>

        {/* Units */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/>
                <line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Units Sold / Product / Month</label>
              <span className="ps-field-val">{unitsSold}</span>
            </div>
          </div>
          <input type="range" min="1" max="500" step="1" value={unitsSold}
            onChange={(e) => setUnitsSold(Number(e.target.value))} className="ps-slider" />
          <div className="ps-slider-labels"><span>1</span><span>500</span></div>
        </div>

        {/* Wholesale */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="1" x2="12" y2="23"/>
                <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Your Wholesale Price / Unit</label>
              <span className="ps-field-val">${wholesalePrice}</span>
            </div>
          </div>
          <input type="range" min="1" max="200" step="1" value={wholesalePrice}
            onChange={(e) => { const w = Number(e.target.value); setWholesale(w); setRetail(w * 2); }}
            className="ps-slider" />
          <div className="ps-slider-labels"><span>$1</span><span>$200</span></div>
        </div>

        {/* Retail */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
                <line x1="7" y1="7" x2="7.01" y2="7"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Platform Retail Price / Unit</label>
              <span className="ps-field-val orange">${retailPrice}</span>
            </div>
          </div>
          <input type="range" min={wholesalePrice} max={wholesalePrice * 5} step="1" value={retailPrice}
            onChange={(e) => setRetail(Number(e.target.value))}
            className="ps-slider ps-slider-orange" />
          <div className="ps-slider-labels"><span>${wholesalePrice}</span><span>${wholesalePrice * 5}</span></div>
        </div>

        {/* Commission */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <line x1="19" y1="5" x2="5" y2="19"/>
                <circle cx="6.5" cy="6.5" r="2.5"/>
                <circle cx="17.5" cy="17.5" r="2.5"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Platform Commission</label>
              <span className="ps-field-val">{commission}%</span>
            </div>
          </div>
          <input type="range" min="5" max="30" step="1" value={commission}
            onChange={(e) => setCommission(Number(e.target.value))} className="ps-slider" />
          <div className="ps-slider-labels"><span>5%</span><span>30%</span></div>
        </div>

        {/* Months */}
        <div className="ps-field">
          <div className="ps-field-row">
            <div className="ps-field-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="4" width="18" height="18" rx="2"/>
                <line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/>
                <line x1="3" y1="10" x2="21" y2="10"/>
              </svg>
            </div>
            <div className="ps-field-meta">
              <label>Projection Period</label>
              <span className="ps-field-val">{months} mo</span>
            </div>
          </div>
          <input type="range" min="1" max="36" step="1" value={months}
            onChange={(e) => setMonths(Number(e.target.value))} className="ps-slider" />
          <div className="ps-slider-labels"><span>1 mo</span><span>36 mo</span></div>
        </div>

        <div className="ps-calc-hint">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#aaa" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
          </svg>
          All calculations are estimates based on the values you provide.
        </div>
      </div>

      {/* RIGHT: Results */}
      <div className="ps-calc-results">
        <div className="ps-proj-header">
          <h3 className="ps-calc-title">Your Projection</h3>
          <div className="ps-live-badge">
            <span className="ps-live-dot" />
            Live Preview
          </div>
        </div>

        {/* Hero */}
        <div className="ps-result-highlight">
          <div className="ps-result-label">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#555" strokeWidth="2.5">
              <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
            </svg>
            EXTRA MONTHLY PROFIT
          </div>
          <div className="ps-result-big">
            {extraPerMonth >= 0 ? "" : "−"}${animatedExtra.toLocaleString("en-US")}
          </div>
          <div className="ps-result-sub">by joining ecomdropship.ai</div>
          <div className="ps-sparkline-wrap">
            <SparklineChart monthly={monthlyWholesale} dropship={monthlyDropship} />
          </div>
          <div className="ps-uplift-chip">
            <div className="ps-uplift-chip-num">{upliftPct > 0 ? `+${upliftPct}%` : "0%"}</div>
            <div className="ps-uplift-chip-lbl">Revenue Uplift</div>
          </div>
        </div>

        {/* Compare */}
        <div className="ps-result-compare">
          <div className="ps-result-card">
            <div className="ps-cmp-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#888" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/>
                <line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/>
              </svg>
            </div>
            <div className="ps-rc-label">Wholesale Only</div>
            <div className="ps-rc-num">${animatedWholesale.toLocaleString("en-US")}</div>
            <div className="ps-rc-sub">per month</div>
          </div>

          <div className="ps-arrow-circle">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M12 5l7 7-7 7"/>
            </svg>
          </div>

          <div className="ps-result-card ps-result-card-orange">
            <div className="ps-cmp-icon ps-cmp-icon-orange">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 10V3L4 14h7v7l9-11h-7z"/>
              </svg>
            </div>
            <div className="ps-rc-label">With EcomDrop.AI</div>
            <div className="ps-rc-num" style={{ color: "var(--orange)" }}>
              ${animatedDropship.toLocaleString("en-US")}
            </div>
            <div className="ps-rc-sub">per month</div>
          </div>
        </div>

        {/* Stats */}
        <div className="ps-result-stats">
          <div className="ps-rs-item">
            <div className="ps-rs-num ps-orange">${animatedUplift.toLocaleString("en-US")}</div>
            <div className="ps-rs-lbl">Over {months} Months</div>
          </div>
          <div className="ps-rs-divider" />
          <div className="ps-rs-item">
            <div className="ps-rs-num">{totalUnits.toLocaleString()}</div>
            <div className="ps-rs-lbl">Units / Month</div>
          </div>
          <div className="ps-rs-divider" />
          <div className="ps-rs-item">
            <div className="ps-rs-num ps-orange">{upliftPct > 0 ? `+${upliftPct}%` : "0%"}</div>
            <div className="ps-rs-lbl">Revenue Uplift</div>
          </div>
        </div>

        {/* Bar comparison with donut */}
        <div className="ps-bar-section">
          <div className="ps-bar-section-title">Monthly Revenue Comparison</div>
          <div className="ps-bar-header">
            <span className="ps-bar-title">Wholesale</span>
            <span className="ps-bar-amount">{fmt(monthlyWholesale)}/mo</span>
          </div>
          <div className="ps-bar-track">
            <div className="ps-bar-fill ps-bar-gray"
              style={{ width: monthlyDropship > 0 ? `${Math.min((monthlyWholesale / monthlyDropship) * 100, 100)}%` : "50%" }} />
          </div>
          <div className="ps-bar-header" style={{ marginTop: "8px" }}>
            <span className="ps-bar-title">With EcomDrop.AI</span>
            <span className="ps-bar-amount orange">{fmt(monthlyDropship)}/mo</span>
          </div>
          <div className="ps-bar-with-donut">
            <div className="ps-bar-track" style={{ flex: 1 }}>
              <div className="ps-bar-fill ps-bar-orange" style={{ width: "100%" }} />
            </div>
            <DonutChart pct={upliftPct} />
          </div>
          <div className="ps-axis-labels">
            <span>0</span><span>5K</span><span>10K</span><span>15K</span><span>20K</span>
          </div>
        </div>

        {upliftPct > 0 && (
          <div className="ps-uplift-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/>
            </svg>
            ↑ {upliftPct}% more revenue — {fmt(totalUplift)} extra over {months} months
          </div>
        )}

        <a href="mailto:join@ecomdropship.ai" className="ps-btn-primary ps-btn-full">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
            <path d="M13 10V3L4 14h7v7l9-11h-7z"/>
          </svg>
          Join Now — It's Free to Start
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14M12 5l7 7-7 7"/>
          </svg>
        </a>
      </div>
    </div>
  </div>
</section>
      {/* ── FOOTER ── */}
      <footer className="ps-footer">
        <div className="ps-footer-inner">
          <div className="ps-footer-logo">ECOM<span>DROP</span>.AI</div>
          <div className="ps-footer-divider" />
          <p className="ps-footer-tagline">
            Helping wholesale suppliers earn 2× revenue through dropshipping.
          </p>
          <div className="ps-footer-links">
            <a href="#how-it-works">How It Works</a>
            <a href="#calculator">Calculator</a>
            <a href="mailto:join@ecomdropship.ai">Contact</a>
          </div>
          <p className="ps-footer-copy">
            © {new Date().getFullYear()} ecomdropship.ai — All rights reserved.
          </p>
        </div>
      </footer>

    </div>
  );
}