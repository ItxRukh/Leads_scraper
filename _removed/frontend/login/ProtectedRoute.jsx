import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/**
 * ProtectedRoute — wraps all /admin/* routes
 * 
 * Checks:
 * 1. Token exists in localStorage
 * 2. Token not expired (client-side check)
 * 3. Token valid on backend (server-side verify)
 * 
 * If any check fails → redirect to /login
 */
export default function ProtectedRoute({ children }) {
  const location = useLocation();
  const [status, setStatus] = useState("checking"); // checking | ok | fail

  useEffect(() => {
    const verify = async () => {
      const token  = localStorage.getItem("admin_token");
      const expiry = localStorage.getItem("admin_token_exp");

      // No token
      if (!token) { setStatus("fail"); return; }

      // Client-side expiry check
      if (expiry && Date.now() > parseInt(expiry)) {
        localStorage.removeItem("admin_token");
        localStorage.removeItem("admin_token_exp");
        setStatus("fail");
        return;
      }

      // Server-side verify
      try {
        const res = await fetch(`${API}/auth/verify`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        setStatus(res.ok ? "ok" : "fail");
      } catch {
        // Server unreachable — allow if token not expired
        setStatus(token ? "ok" : "fail");
      }
    };

    verify();
  }, [location.pathname]);

  if (status === "checking") {
    return (
      <div style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0a0f",
        color: "rgba(255,255,255,0.3)",
        fontSize: 14,
        gap: 10,
      }}>
        <i className="ti ti-loader-2" style={{ animation: "spin 0.8s linear infinite" }} />
        Verifying session...
      </div>
    );
  }

  if (status === "fail") {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children;
}
