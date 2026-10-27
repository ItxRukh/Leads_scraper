import { useCallback, useEffect, useMemo, useState } from "react";
import { ConnectionsContext } from "./connectionsContext";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const POLL_MS = 15000;

/**
 * Keeps the state of the email connection up to date
 * (one request every 15 s, shared by the navbar chips and the warning banner).
 */
export function ConnectionsProvider({ children }) {
  const [data, setData] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API}/connections`);
      if (res.ok) setData(await res.json());
    } catch {
      // The backend is unreachable: keep the last known state.
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const value = useMemo(() => ({ data, refresh }), [data, refresh]);
  return <ConnectionsContext.Provider value={value}>{children}</ConnectionsContext.Provider>;
}
