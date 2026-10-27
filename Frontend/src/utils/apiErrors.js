// apiErrors.js — turn a failed API response into a sentence a person can read.
// FastAPI answers with either a plain string or a list of validation problems.

export function errorText(detail) {
  if (!detail) return "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => {
      const where = (d.loc ?? []).slice(1)
        .filter(x => x !== "config" && x !== "rules")
        .map(x => (typeof x === "number" ? `#${x + 1}` : x))
        .join(" → ");
      const msg = String(d.msg ?? "").replace(/^Value error, /, "");
      return where ? `${where}: ${msg}` : msg;
    }).join("; ");
  }
  return String(detail);
}

export async function readError(res) {
  try { return errorText((await res.json()).detail); }
  catch { return "Something went wrong."; }
}
