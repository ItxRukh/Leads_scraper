import { useTheme } from "../theme";

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const toLight = theme === "dark";
  const label = toLight ? "Switch to light mode" : "Switch to dark mode";

  return (
    <button className="nb-icon-btn" onClick={toggle} aria-label={label} title={label}>
      <i className={`ti ${toLight ? "ti-sun" : "ti-moon"}`} aria-hidden="true" />
    </button>
  );
}
