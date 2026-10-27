import { useState } from "react";
import Sidebar from "./Sidebar";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { ConnectionBanner } from "./ConnectionStatus";
import "./AdminLayout.css";

export default function AdminLayout({ children, leadsCount, hotCount, notifCount }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="admin-layout">
      {/* mobile overlay — tap outside to close */}
      {sidebarOpen && (
        <div
          className="sb-mobile-overlay"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <Sidebar
        leadsCount={leadsCount}
        hotCount={hotCount}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="admin-main">
        <Navbar
          notifCount={notifCount}
          onMenuClick={() => setSidebarOpen((o) => !o)}
        />
        <main className="admin-body">
          <ConnectionBanner />
          {children}
        </main>
        <Footer />
      </div>
    </div>
  );
}