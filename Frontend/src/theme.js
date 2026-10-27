import { useCallback, useEffect, useState } from "react";

const KEY = "theme";
const root = () => document.documentElement;

// index.html sets data-theme before first paint; this reads it back.
export function currentTheme() {
  return root().dataset.theme === "light" ? "light" : "dark";
}

export function setTheme(theme) {
  root().dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* private mode: the choice just isn't remembered */ }
}

// Turn on the colour transition only after the first paint, so the page does not fade in from the wrong theme.
export function enableThemeTransitions() {
  requestAnimationFrame(() => requestAnimationFrame(() => root().classList.add("theme-ready")));
}

export function useTheme() {
  const [theme, setState] = useState(currentTheme);

  const toggle = useCallback(() => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    setTheme(next);
    setState(next);
  }, []);

  // keep several open tabs in step
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === KEY && (e.newValue === "light" || e.newValue === "dark")) {
        root().dataset.theme = e.newValue;
        setState(e.newValue);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return { theme, toggle };
}
