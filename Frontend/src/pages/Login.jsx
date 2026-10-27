import { useState } from "react";
import { login } from "../auth";
import { BRAND } from "../brand";
import "./Login.css";

export default function Login({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      onSuccess();
    } catch (err) {
      setError(err.message || "Login failed");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lg-page">
      <form className="lg-card" onSubmit={submit}>
        <h1 className="lg-title">{BRAND.name}</h1>
        <p className="lg-sub">Sign in to the admin panel</p>

        <label className="lg-label" htmlFor="lg-user">Username</label>
        <input id="lg-user" className="lg-input" value={username} autoComplete="username"
          onChange={(e) => setUsername(e.target.value)} autoFocus required />

        <label className="lg-label" htmlFor="lg-pass">Password</label>
        <input id="lg-pass" className="lg-input" type="password" value={password} autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)} required />

        {error && <div className="lg-error" role="alert">{error}</div>}

        <button className="lg-btn" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
