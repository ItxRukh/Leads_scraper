import { NavLink } from "react-router-dom";
import { useState, useEffect } from "react";
import { getSettings } from "../pages/Settings";
import "./Sidebar.css";

const NAV = [
  {
    section: "General",
    items: [
      { to: "/admin/analytics", icon: "ti-chart-bar", label: "Analytics" },
    ],
  },
  {
    section: "Outreach",
    items: [
      { to: "/admin/leads",    icon: "ti-users",          label: "Leads",         badge: null, badgeType: "count" },
      { to: "/admin/queue",    icon: "ti-list-check",     label: "Outreach queue" },
      { to: "/admin/email",    icon: "ti-mail",           label: "Email outreach" },
      { to: "/admin/calls",    icon: "ti-phone",          label: "Call logs" },
      { to: "/admin/whatsapp", icon: "ti-brand-whatsapp", label: "WhatsApp" },
    ],
  },
  {
    section: "CRM",
    items: [
      { to: "/admin/pipeline", icon: "ti-layout-kanban",  label: "Pipeline", badge: "hot", badgeType: "hot" },
    ],
  },
  {
    section: "Settings",
    items: [
      { to: "/admin/profiles", icon: "ti-briefcase", label: "Industry profiles" },
      { to: "/admin/dnc",      icon: "ti-ban",      label: "DNC / Blacklist" },
      { to: "/admin/settings", icon: "ti-settings", label: "Settings" },
    ],
  },
];

export default function Sidebar({ leadsCount = 0, hotCount = 0, isOpen = false, onClose }) {
  const [companyName, setCompanyName] = useState("EcomDrop.ai");

  useEffect(() => {
    setCompanyName(getSettings().companyName || "EcomDrop.ai");
    // Pick up changes saved on the Settings page without needing a full reload
    const onStorage = () => setCompanyName(getSettings().companyName || "EcomDrop.ai");
    window.addEventListener("storage", onStorage);
    const t = setInterval(onStorage, 3000);
    return () => { window.removeEventListener("storage", onStorage); clearInterval(t); };
  }, []);

  function getBadge(item) {
    if (!item.badge) return null;
    if (item.badgeType === "count" && leadsCount > 0)
      return <span className="sb-badge sb-badge--or">{leadsCount.toLocaleString()}</span>;
    if (item.badgeType === "hot" && hotCount > 0)
      return <span className="sb-badge sb-badge--green">{hotCount} hot</span>;
    return null;
  }

  return (
    <aside className={`sidebar${isOpen ? " sidebar--open" : ""}`}>
      <button
        className="sb-close-btn"
        onClick={onClose}
        aria-label="Close menu"
      >
        <i className="ti ti-x" />
      </button>

      <div className="sb-logo">
        <div className="sb-logo-text">
          {companyName === "EcomDrop.ai"
            ? <>ECOM<span>DROP</span>.AI</>
            : companyName}
        </div>
        <div className="sb-logo-sub">Admin panel</div>
      </div>

      <nav className="sb-nav">
        {NAV.map((group, gi) => (
          <div key={gi} className="sb-group">
            <div className="sb-section-label">{group.section}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  "sb-item" + (isActive ? " active" : "")
                }
                onClick={onClose}
              >
                <i className={`ti ${item.icon}`} aria-hidden="true" />
                <span>{item.label}</span>
                {getBadge(item)}
              </NavLink>
            ))}
            {gi < NAV.length - 1 && <div className="sb-divider" />}
          </div>
        ))}
      </nav>
    </aside>
  );
}