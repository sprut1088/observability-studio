import axios from "axios";

const api = axios.create({
  baseURL: "http://20.193.248.157:8000/api",
});

export const validateTool = (payload) => api.post("/validate", payload);
export const exportExcel = (payload) => api.post("/export", payload);
export const runAssessment = (payload) => api.post("/assess", payload);

export default api;
