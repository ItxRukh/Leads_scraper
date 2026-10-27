import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "./Login.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export default function Login() {
  const navigate  = useNavigate();
  const [form,    setForm]    = useState({ username: "", password: "" });
  const [error,   setError]   = useState("");
  const [loading, setLoading] = useState(false);
  const [showPwd, setShowPwd] = useState(false);

  const handleSubmit = async () => {
    if (!form.username.trim() || !form.password.trim()) {
      setError("Please enter username and password");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.username.trim(),
          password: form.password,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail ?? "Invalid credentials");
        return;
      }

      // Store token securely
      localStorage.setItem("admin_token", data.access_token);
      localStorage.setItem("admin_token_exp", 
        String(Date.now() + data.expires_in * 1000)
      );

      navigate("/admin/analytics", { replace: true });

    } catch {
      setError("Cannot connect to server. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter") handleSubmit();
  };

  return (
    <div className="login-page">
      {/* background blobs */}
      <div className="login-blob login-blob--1" />
      <div className="login-blob login-blob--2" />

      <div className="login-card">

        {/* logo */}
        <div className="login-logo">
          <div className="login-logo-icon">
            <i className="ti ti-building-store" />
          </div>
          <div className="login-logo-text">
            ECOM<span>DROP</span>.AI
          </div>
          <div className="login-logo-sub">Admin Panel</div>
        </div>

        <div className="login-divider" />

        {/* form */}
        <div className="login-form">

          <div className="lf-group">
            <label>Username</label>
            <div className="lf-input-wrap">
              <i className="ti ti-user" />
              <input
                type="text"
                placeholder="Enter username"
                value={form.username}
                onChange={(e) => setForm(f => ({ ...f, username: e.target.value }))}
                onKeyDown={handleKey}
                autoComplete="username"
                autoFocus
              />
            </div>
          </div>

          <div className="lf-group">
            <label>Password</label>
            <div className="lf-input-wrap">
              <i className="ti ti-lock" />
              <input
                type={showPwd ? "text" : "password"}
                placeholder="Enter password"
                value={form.password}
                onChange={(e) => setForm(f => ({ ...f, password: e.target.value }))}
                onKeyDown={handleKey}
                autoComplete="current-password"
              />
              <button
                className="lf-eye"
                onClick={() => setShowPwd(s => !s)}
                tabIndex={-1}
                type="button"
              >
                <i className={`ti ${showPwd ? "ti-eye-off" : "ti-eye"}`} />
              </button>
            </div>
          </div>

          {/* error */}
          {error && (
            <div className="lf-error">
              <i className="ti ti-alert-circle" />
              {error}
            </div>
          )}

          <button
            className="lf-submit"
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? (
              <><i className="ti ti-loader-2 spin" /> Signing in...</>
            ) : (
              <><i className="ti ti-login" /> Sign In</>
            )}
          </button>

        </div>

        <div className="login-footer">
          Secured with JWT · Session expires in 8 hours
        </div>

      </div>
    </div>
  );
}