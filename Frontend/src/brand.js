// One place for the company identity shown in the admin panel.
export const BRAND = {
  name: "LEADWREN",
  displayName: "Leadwren",
  shortName: "Leadwren",
  tagline: "Outreach console",
  contactEmail: import.meta.env.VITE_CONTACT_EMAIL || "",
};

// Values the old EcomDrop setup saved in this browser; replaced by the new defaults on read.
const LEGACY = {
  companyName: ["EcomDrop.ai", "Cascade Software Solutions"],
  emailSubject: ["Grow your ecom store with EcomDrop"],
};
export function migrateLegacy(saved, defaults) {
  const out = { ...saved };
  for (const [key, olds] of Object.entries(LEGACY)) {
    if (olds.includes(out[key])) out[key] = defaults[key];
  }
  return out;
}
