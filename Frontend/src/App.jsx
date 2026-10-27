import { Routes, Route, Navigate } from "react-router-dom";
import AdminLayout     from "./components/AdminLayout";
import { ConnectionsProvider } from "./components/ConnectionsProvider";

import Leads           from "./pages/Leads";
import EmailOutreach   from "./pages/EmailOutreach";
import CallLogs        from "./pages/CallLogs";
import WhatsApp        from "./pages/WhatsApp";
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

export default function App() {
  return (
    <ConnectionsProvider>
    <Routes>
      <Route path="/admin/analytics" element={page(Analytics)} />
      <Route path="/admin/leads"     element={page(Leads)} />
      <Route path="/admin/queue"     element={page(OutreachQueue)} />
      <Route path="/admin/email"     element={page(EmailOutreach)} />
      <Route path="/admin/calls"     element={page(CallLogs)} />
      <Route path="/admin/whatsapp"  element={page(WhatsApp)} />
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
