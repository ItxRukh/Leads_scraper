import { BRAND } from "../brand";
import "./Footer.css";

export default function Footer() {
  return (
    <footer className="admin-footer">
      <div className="ft-logo">{BRAND.name}</div>

      {BRAND.contactEmail && (
        <div className="ft-links">
          <a href={`mailto:${BRAND.contactEmail}`}>Contact</a>
        </div>
      )}

      <p className="ft-copy">
        © {new Date().getFullYear()} {BRAND.displayName}. All rights reserved.
      </p>
    </footer>
  );
}
