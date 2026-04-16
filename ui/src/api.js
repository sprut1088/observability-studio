import axios from "axios";

export const API_HOST = "http://20.193.248.157:8000";

const api = axios.create({
  baseURL: `${API_HOST}/api`,
});

export const validateTool = (payload) => api.post("/validate", payload);
export const exportExcel = (payload) => api.post("/export", payload);
export const runAssessment = (payload) => api.post("/assess", payload);

export default api;
