import axios, { AxiosError } from "axios";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: `${API_BASE}/api`,
  timeout: 30_000,
});

export const apiSlow = axios.create({
  baseURL: `${API_BASE}/api`,
  timeout: 120_000,
});

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("trading-auth");
    if (!raw) return null;
    return JSON.parse(raw)?.state?.token ?? null;
  } catch {
    return null;
  }
}

function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  try {
    const raw = localStorage.getItem("trading-auth");
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed?.state) {
      parsed.state.token    = null;
      parsed.state.username = null;
      localStorage.setItem("trading-auth", JSON.stringify(parsed));
    }
  } catch { /* ignore */ }
}

function attachAuth(config: any) {
  const token = getStoredToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
}

function on401(err: AxiosError) {
  if (err.response?.status === 401 && typeof window !== "undefined") {
    clearStoredToken();
    window.location.href = "/login";
  }
  return Promise.reject(err);
}

api.interceptors.request.use(attachAuth);
apiSlow.interceptors.request.use(attachAuth);
api.interceptors.response.use((r) => r, on401);
apiSlow.interceptors.response.use((r) => r, on401);

export function getErrorMessage(err: any): string {
  if (!err) return "Unknown error";

  if (err.response?.data?.detail) {
    const d = err.response.data.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e: any) => e.msg ?? String(e)).join("; ");
    return String(d);
  }

  if (err.response?.data?.message) return String(err.response.data.message);

  if (err.code === "ECONNABORTED") {
    return "Request timed out — the backend is taking too long. Try again or check backend health.";
  }

  if (!err.response) {
    return (
      `Cannot reach backend (${API_BASE}). ` +
      "Check: (1) NEXT_PUBLIC_API_URL env var on Vercel, " +
      "(2) CORS_ORIGINS=* on Railway backend, " +
      "(3) backend service is running."
    );
  }

  switch (err.response.status) {
    case 400: return `Bad request: ${err.response.data?.detail ?? "check your input"}`;
    case 403: return "Access denied";
    case 404: return "Not found — the resource does not exist";
    case 422: return "Validation error — check your input data";
    case 429: return "Too many requests — please wait a moment";
    case 500: return "Internal server error — check Railway logs";
    case 502: return "Backend unreachable — Railway service may be starting up";
    case 503: return "Service unavailable — backend is overloaded";
    default:  return err.message || `HTTP ${err.response.status} error`;
  }
}

export async function stepOneLogin(username: string, password: string) {
  const { data } = await apiSlow.post("/auth/login", { username, password });
  return data;
}

export async function stepTwoVerifyOTP(username: string, otp: string) {
  const { data } = await apiSlow.post("/auth/verify-otp", { username, otp });
  return data;
}

export function logout() {
  clearStoredToken();
  window.location.href = "/login";
}

export function getToken(): string | null { return getStoredToken(); }
export function isAuthenticated(): boolean { return !!getStoredToken(); }

export const fetcher = (url: string) => api.get(url).then((r) => r.data);
