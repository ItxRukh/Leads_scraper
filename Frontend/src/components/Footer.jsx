import "./Footer.css";

export default function Footer() {
  return (
    <footer className="admin-footer">
      <div className="ft-logo">
        ECOM<span>DROP</span>.AI
      </div>

      <div className="ft-links">
        <a href="mailto:support@ecomdropship.ai">Contact</a>
        <a href="/privacy">Privacy policy</a>
        <a href="/terms">Terms of service</a>
      </div>

      <p className="ft-copy">
        © {new Date().getFullYear()} ecomdropship.ai — All rights reserved.
      </p>
    </footer>
  );
}
