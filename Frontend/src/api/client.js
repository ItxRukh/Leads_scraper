// client.js — Axios instance for FastAPI backend
import axios from "axios";

// In dev, talk to the local FastAPI server on :8000.
// In production, the frontend is served BY the same FastAPI backend
// (same origin), so an empty baseURL just means "call the current domain" —
// no CORS, no hardcoded URL to forget about.
const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    "Content-Type": "application/json",
  },
});

// ── Leads ──────────────────────────────────────────────────
export const leadsAPI = {
  getAll: (params) => client.get("/leads", { params }),
  getById: (id) => client.get(`/leads/${id}`),
  getByTier: (tier) => client.get("/leads", { params: { tier } }),
  exportCSV: (params) => client.get("/leads/export/csv", { params }),  // was /leads/export
};

// ── Email Logs ─────────────────────────────────────────────
export const emailAPI = {
  getAll: () => client.get("/email-logs"),                             // was /emails
  getByLead: (leadId) => client.get("/email-logs", { params: { lead_id: leadId } }),
};

// ── Call Logs ──────────────────────────────────────────────
export const callAPI = {
  getAll: () => client.get("/call-logs"),                              // was /calls
  getByLead: (leadId) => client.get("/call-logs", { params: { lead_id: leadId } }),
};

// ── WhatsApp Logs ──────────────────────────────────────────
export const whatsappAPI = {
  getAll: () => client.get("/whatsapp-logs"),                          // was /whatsapp
  getByLead: (leadId) => client.get("/whatsapp-logs", { params: { lead_id: leadId } }),
};

// ── Analytics ──────────────────────────────────────────────
export const analyticsAPI = {
  getOverview: () => client.get("/analytics/overview"),                // was /analytics/summary
};

// ── DNC ────────────────────────────────────────────────────
export const dncAPI = {
  getAll: () => client.get("/dnc"),
  add: (data) => client.post("/dnc", data),
  remove: ({ email, phone, domain }) =>
    client.delete("/dnc", { params: { email, phone, domain } }),       // was /dnc/:id
  scanReplies: () => client.post("/dnc/scan-replies"),
  exportCSV: () => client.get("/dnc/export"),
};

// ── Pipeline Triggers ──────────────────────────────────────
export const pipelineAPI = {
  scrape: (data) => client.post("/pipeline/scrape", data),
  linkedin: () => client.post("/pipeline/linkedin"),
  audit: (leadId = null) =>
    client.post("/pipeline/audit", null, { params: leadId ? { lead_id: leadId } : {} }),
  verifyEmails: () => client.post("/pipeline/verify-emails"),
  sendEmails: (data) => client.post("/pipeline/send-emails", data),
  vapiCalls: (data) => client.post("/pipeline/vapi-calls", data),
  whatsapp: (data) => client.post("/pipeline/whatsapp", data),
  getSummary: () => client.get("/pipeline/summary"),
};

// ── CRM ────────────────────────────────────────────────────
export const crmAPI = {
  updateStage: (leadId, stage) =>
    client.put(`/leads/${leadId}/pipeline`, { stage }),
};

// ── Notifications ──────────────────────────────────────────
export const notificationsAPI = {
  getAll: () => client.get("/notifications"),
  markRead: () => client.post("/notifications/mark-read"),
};

// ── Calculator ─────────────────────────────────────────────
export const calculatorAPI = {
  calculate: (data) => client.post("/calculator", data),
};

// ── Scraping Jobs History ──────────────────────────────────
export const scrapingJobsAPI = {
  getAll: () => client.get("/scraping-jobs"),
};

export default client;