import { useCallback, useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import AdminLayout     from "./components/AdminLayout";
import { ConnectionsProvider } from "./components/ConnectionsProvider";
import { fetchAuthStatus, getToken, LOGOUT_EVENT } from "./auth";

import Login           from "./pages/Login";
import Import          from "./pages/Import";
import Leads           from "./pages/Leads";
import EmailOutreach   from "./pages/EmailOutreach";
import Pipeline        from "./pages/Pipeline";
import Analytics       from "./pages/Analytics";
import DNC             from "./pages/DNC";
import Settings        from "./pages/Settings";
import Profiles        from "./pages/Profiles";
import OutreachQueue   from "./pages/OutreachQueue";

// Every admin page sits inside the same layout (sidebar + navbar + footer).
const page = (Component) => (
  <AdminLayout><Component /></AdminLayout>
);

function AdminRoutes() {
  return (
    <ConnectionsProvider>
    <Routes>
      <Route path="/admin/analytics" element={page(Analytics)} />
      <Route path="/admin/import"    element={page(Import)} />
      <Route path="/admin/leads"     element={page(Leads)} />
      <Route path="/admin/queue"     element={page(OutreachQueue)} />
      <Route path="/admin/email"     element={page(EmailOutreach)} />
      <Route path="/admin/pipeline"  element={page(Pipeline)} />
      <Route path="/admin/dnc"       element={page(DNC)} />
      <Route path="/admin/profiles"  element={page(Profiles)} />
      <Route path="/admin/settings"  element={page(Settings)} />

      {/* Anything else (including "/" and old /login links) opens the dashboard */}
      <Route path="*" element={<Navigate to="/admin/analytics" replace />} />
    </Routes>
    </ConnectionsProvider>
  );
}

function Notice({ children, action }) {
  return (
    <div className="lg-page">
      <div className="lg-card" style={{ textAlign: "center" }}>
        <p className="lg-sub" style={{ margin: 0 }}>{children}</p>
        {action}
      </div>
    </div>
  );
}

// "checking" -> asks the backend whether a login is needed; "login" -> shows the form; "ready" -> the app.
export default function App() {
  const [state, setState] = useState("checking");

  const check = useCallback(async () => {
    setState("checking");
    try {
      const required = await fetchAuthStatus();
      setState(required && !getToken() ? "login" : "ready");
    } catch {
      setState("offline");
    }
  }, []);

  useEffect(() => {
    check();
    const onLogout = () => setState("login");
    window.addEventListener(LOGOUT_EVENT, onLogout);
    return () => window.removeEventListener(LOGOUT_EVENT, onLogout);
  }, [check]);

  if (state === "checking") return <Notice>Loading…</Notice>;
  if (state === "offline") {
    return (
      <Notice action={<button className="lg-btn" onClick={check}>Try again</button>}>
        Cannot reach the backend. Start it, then try again.
      </Notice>
    );
  }
  if (state === "login") return <Login onSuccess={() => setState("ready")} />;
  return <AdminRoutes />;
}
