import axios from "axios";

export const API_HOST =
  import.meta.env.VITE_API_BASE_URL || "http://10.235.21.132:8001";

const api = axios.create({
  baseURL: `${API_HOST}/api`,
});

// ── Legacy endpoints
export const validateTool = (payload) => api.post("/validate", payload);
export const exportExcel = (payload) => api.post("/export", payload);
export const runAssessment = (payload) => api.post("/assess", payload);
export const runRedIntelligence = (payload) =>
  api.post("/red-intelligence", payload);
export const runObservabilityGapMap = (payload) =>
  api.post("/observability-gap-map", payload);

// ── Hub v1 endpoints
export const v1Validate = (payload) => api.post("/v1/validate", payload);
export const v1Crawl = (payload) => api.post("/v1/crawl", payload);
export const v1Assess = (payload) => api.post("/v1/assess", payload);

// ── RCA Agent
export const v1Rca = (payload) => api.post("/v1/rca", payload);

// ── ObsCo — Observability Copilot (floating chat bot)
export const obscoChat = (payload) => api.post("/v1/obsco/chat", payload);

// ── SLO Studio
export const v1SloStudio = (payload) => api.post("/v1/slo-studio", payload);

// ── Platform feature flags
export const getFeatureFlags = () => api.get("/feature-flags");

export default api;

export function runAyosaInvestigation(payload) {
  return api.post("/ayosa/chat", payload);
}

/**
 * Streaming investigation — parses SSE from /api/ayosa/chat/stream.
 * Calls onEvent for each parsed event object:
 *   { type: "step",      index, label, status }
 *   { type: "llm_chunk", text }
 *   { type: "result",    data }
 *   { type: "error",     message }
 * Returns a Promise that resolves when the stream closes.
 */
export async function streamAyosaInvestigation(payload, onEvent) {
  const response = await fetch(`${API_HOST}/api/ayosa/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errBody = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(errBody.detail || response.statusText);
  }

  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        // ignore malformed SSE lines
      }
    }
  }
}

export function generateAyosaRunbook(payload) {
  return api.post("/ayosa/runbook", payload);
}