// Admin login on the browser side: keeps the token, adds it to every API call, and reacts to a 401.
const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://localhost:8000" : "");
const KEY = "auth_token";
export const LOGOUT_EVENT = "auth:logout";

export function getToken() {
  try { return localStorage.getItem(KEY) || ""; } catch { return ""; }
}

export function setToken(token) {
  try { localStorage.setItem(KEY, token); } catch { /* private mode: login lasts until the tab closes only if storage works */ }
}

export function logout() {
  try { localStorage.removeItem(KEY); } catch { /* nothing to clear */ }
  window.dispatchEvent(new Event(LOGOUT_EVENT));
}

const isApiUrl = (url) => typeof url === "string" && (API ? url.startsWith(API) : url.startsWith("/"));
const isLoginCall = (url) => url.includes("/auth/login");

// Most pages call fetch() directly, so the token is added in one place instead of in 47 call sites.
export function installAuthFetch() {
  if (window.__authFetchInstalled) return;
  window.__authFetchInstalled = true;
  const original = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url;
    if (!isApiUrl(url)) return original(input, init);

    const token = getToken();
    const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);

    const res = await original(input, { ...init, headers });
    if (res.status === 401 && !isLoginCall(url) && getToken()) logout();   // token expired or revoked
    return res;
  };
}

export async function fetchAuthStatus() {
  const res = await fetch(`${API}/auth/status`);
  if (!res.ok) throw new Error(`status ${res.status}`);
  return (await res.json()).auth_required;
}

export async function login(username, password) {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || "Login failed");
  setToken(body.token);
}

// Plain links cannot send the token, so exports are fetched with it and saved from memory.
export async function downloadFile(path, fallbackName = "export.csv") {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const disposition = res.headers.get("content-disposition") || "";
  const name = /filename="?([^";]+)"?/i.exec(disposition)?.[1] || fallbackName;

  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
