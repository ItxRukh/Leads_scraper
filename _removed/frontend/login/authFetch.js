// authFetch.js — drop-in replacement for the native fetch()
// -----------------------------------------------------------
// Every backend endpoint (except the health check "/" and the public
// "/calculator" widget) now requires a valid admin JWT (see Backend/app.py).
// Swapping plain `fetch(...)` calls for `authFetch(...)` automatically:
//   1. Attaches `Authorization: Bearer <token>` from localStorage
//   2. Redirects to /login if the backend responds with 401 (missing/expired token)
//
// Usage is identical to fetch():
//   const res = await authFetch(`${API}/leads`);

export function authFetch(url, options = {}) {
  const token = localStorage.getItem("admin_token");

  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  return fetch(url, { ...options, headers }).then((res) => {
    if (res.status === 401) {
      localStorage.removeItem("admin_token");
      localStorage.removeItem("admin_token_exp");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return res;
  });
}