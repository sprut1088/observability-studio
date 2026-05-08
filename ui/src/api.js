import axios from "axios";

export const API_HOST = "http://10.235.21.132:8001";

const api = axios.create({
  baseURL: `${API_HOST}/api`,
});

// ── Legacy endpoints (full multi-tool workflow) ──────────
export const validateTool  = (payload) => api.post("/validate", payload);
export const exportExcel   = (payload) => api.post("/export",   payload);
export const runAssessment = (payload) => api.post("/assess",   payload);
export const runRedIntelligence = (payload) => api.post("/red-intelligence", payload);
export const runObservabilityGapMap = (payload) => api.post("/observability-gap-map", payload);

// ── Hub v1 endpoints (single-tool, streamlined) ──────────
export const v1Validate = (payload) => api.post("/v1/validate", payload);
export const v1Crawl    = (payload) => api.post("/v1/crawl",    payload);
export const v1Assess   = (payload) => api.post("/v1/assess",   payload);

// ── RCA Agent ─────────────────────────────────────────────
export const v1Rca = (payload) => api.post("/v1/rca", payload);

// ── Incident Simulator ───────────────────────────────────
export const runIncidentSimulation = (payload) => api.post("/incident-simulator", payload);

// ── Platform feature flags ────────────────────────────────
export const getFeatureFlags = () => api.get("/feature-flags");

export default api;
