import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { notificationsAPI } from "../api/client";
import "./Navbar.css";
import { ConnectionChips } from "./ConnectionStatus";
import ThemeToggle from "./ThemeToggle";
import { BRAND } from "../brand";
import { getToken, logout } from "../auth";

const PAGE_TITLES = {
  "/admin/analytics": { label: "Analytics",       sub: "Funnel, conversion and campaign stats" },
  "/admin/import":    { label: "Import leads",    sub: "Upload or paste leads, clean them and check for duplicates" },
  "/admin/leads":     { label: "Leads",            sub: "All scraped and scored supplier leads" },
  "/admin/queue":     { label: "Outreach queue",   sub: "Who to contact next, and when" },
  "/admin/email":     { label: "Email outreach",   sub: "Campaigns via Instantly" },
  "/admin/pipeline":  { label: "Pipeline",         sub: "CRM — track every lead stage" },
  "/admin/dnc":       { label: "DNC / Blacklist",  sub: "Do-not-contact list management" },
  "/admin/profiles":  { label: "Industry profiles", sub: "Search, scoring and offer for each industry" },
  "/admin/settings":  { label: "Settings",         sub: "API keys and platform configuration" },
};

const TYPE_META = {
  email_reply:     { icon: "ti-mail-opened",       label: "Email reply" },
  sms_reply:       { icon: "ti-message-2",         label: "SMS reply" },
  callback:        { icon: "ti-phone-incoming",    label: "Callback" },
  no_email_digest: { icon: "ti-mail-off",          label: "Weekly digest" },
};

// Backend sends naive UTC timestamps (no "Z"/offset), which JS's Date()
// would otherwise misread as local time. Force UTC interpretation here,
// then toLocaleString() below converts it to the browser's local time.
function toUtcDate(iso) {
  if (!iso) return null;
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTz ? iso : `${iso}Z`);
}

function timeAgo(iso) {
  if (!iso) return "";
  const diffMs = Date.now() - toUtcDate(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatFullDate(iso) {
  if (!iso) return "";
  return toUtcDate(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function Navbar({ onMenuClick }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const [items, setItems] = useState([]);
  const [activeItem, setActiveItem] = useState(null); // item shown in full-content modal
  const dropdownRef = useRef(null);

  const current = PAGE_TITLES[location.pathname] ?? { label: "Admin", sub: "" };

  const fetchNotifications = useCallback(() => {
    notificationsAPI
      .getAll()
      .then((res) => {
        setNotifCount(res.data.unread_count ?? 0);
        setItems(res.data.items ?? []);
      })
      .catch(() => {
        // Silently ignore — bell just stays at its last known state
      });
  }, []);

  useEffect(() => {
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 30000); // poll every 30s
    return () => clearInterval(interval);
  }, [fetchNotifications]);

  // Close dropdown on outside click (but not when the modal is open —
  // the modal has its own overlay/close handling)
  useEffect(() => {
    function handleClickOutside(e) {
      if (activeItem) return;
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [activeItem]);

  const handleBellClick = () => {
    const willOpen = !open;
    setOpen(willOpen);
    if (willOpen && notifCount > 0) {
      // Zero the badge, but keep showing the items list (read + unread)
      notificationsAPI.markRead().then(() => {
        setNotifCount(0);
        setItems((prev) => prev.map((i) => ({ ...i, is_read: true })));
      });
    }
  };

  const handleItemClick = (item) => {
    // Show the full message in a modal instead of navigating away —
    // a one-line preview isn't enough to actually read an email reply.
    setActiveItem(item);
  };

  const handleViewLead = () => {
    if (!activeItem) return;
    setActiveItem(null);
    setOpen(false);
    if (activeItem.type === "no_email_digest") {
      // No single lead_id here — this item is a summary of many leads,
      // so send the admin to the Leads page with the Stuck Leads filter
      // already applied (Feature 6) instead of a single lead detail.
      navigate(`/admin/leads?stuck=true`);
    } else {
      navigate(`/admin/leads?lead=${activeItem.lead_id}`);
    }
  };

  return (
    <header className="navbar">
      {/* hamburger — only visible on mobile */}
      <button
        className="nb-hamburger"
        onClick={onMenuClick}
        aria-label="Open menu"
      >
        <i className="ti ti-menu-2" />
      </button>

      <div className="nb-left">
        <div className="nb-page-info">
          <h1 className="nb-page-title">{current.label}</h1>
          <p className="nb-page-sub">{current.sub}</p>
        </div>
      </div>

      <div className="nb-right">
        <ConnectionChips />
        <ThemeToggle />
        {getToken() && (
          <button className="nb-icon-btn" onClick={logout} aria-label="Sign out" title="Sign out">
            <i className="ti ti-logout" aria-hidden="true" />
          </button>
        )}
        <div className="nb-notif-wrap" ref={dropdownRef}>
          <button
            className="nb-icon-btn"
            aria-label="Notifications"
            onClick={handleBellClick}
          >
            <i className="ti ti-bell" aria-hidden="true" />
            {notifCount > 0 && (
              <span className="nb-notif-dot">{notifCount > 9 ? "9+" : notifCount}</span>
            )}
          </button>

          {open && (
            <div className="nb-notif-dropdown">
              <div className="nb-notif-header">Notifications</div>
              {items.length === 0 ? (
                <div className="nb-notif-empty">
                  No replies or callbacks yet. New activity from leads
                  (email replies, SMS replies, callbacks) will show up here.
                </div>
              ) : (
                <div className="nb-notif-list">
                  {items.map((item) => {
                    const meta = TYPE_META[item.type] ?? { icon: "ti-bell", label: item.type };
                    return (
                      <button
                        key={`${item.type}-${item.id}`}
                        className={`nb-notif-item${item.is_read ? " nb-notif-item-read" : ""}`}
                        onClick={() => handleItemClick(item)}
                      >
                        {!item.is_read && <span className="nb-notif-item-unread-dot" />}
                        <i className={`ti ${meta.icon} nb-notif-item-icon`} aria-hidden="true" />
                        <div className="nb-notif-item-body">
                          <div className="nb-notif-item-top">
                            <span className="nb-notif-item-type">{meta.label}</span>
                            <span className="nb-notif-item-time">{timeAgo(item.timestamp)}</span>
                          </div>
                          {item.lead_name && <div className="nb-notif-item-lead">{item.lead_name}</div>}
                          <div className="nb-notif-item-preview">{item.preview}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="nb-avatar-wrap">
          <div className="nb-avatar">AD</div>
          <div className="nb-user">
            <span className="nb-user-name">Admin</span>
            <span className="nb-user-role">{BRAND.shortName}</span>
          </div>
        </div>
      </div>

      {/* ── Full-content modal — shown when a notification item is clicked ── */}
      {activeItem && (
        <div className="nb-modal-overlay" onClick={() => setActiveItem(null)}>
          <div className="nb-modal" onClick={(e) => e.stopPropagation()}>
            <div className="nb-modal-header">
              <div>
                <div className="nb-modal-title">
                  {(TYPE_META[activeItem.type] ?? {}).label ?? activeItem.type}
                  {activeItem.lead_name ? ` — ${activeItem.lead_name}` : ""}
                </div>
                <div className="nb-modal-sub">{formatFullDate(activeItem.timestamp)}</div>
              </div>
              <button className="nb-modal-close" onClick={() => setActiveItem(null)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="nb-modal-body">
              {activeItem.full_content || activeItem.preview || "(no content)"}
            </div>
            <div className="nb-modal-footer">
              <button className="nb-modal-view-btn" onClick={handleViewLead}>
                {activeItem.type === "no_email_digest" ? "View leads" : "View lead"} <i className="ti ti-arrow-right" />
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}