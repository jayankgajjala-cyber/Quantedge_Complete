import axios, { AxiosError } from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Default client — 30s timeout for normal reads/mutations
export const api = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 30_000,
});

// Long-running operations (backtest = 10yr data fetch + 8 strategies)
export const apiSlow = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 120_000,
});

// ── Token helpers ─────────────────────────────────────────────────────────────
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

// ── Attach JWT to both clients ────────────────────────────────────────────────
function attachAuth(config: any) {
  const token = getStoredToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
}

api.interceptors.request.use(attachAuth);
apiSlow.interceptors.request.use(attachAuth);

// 401 → clear token and redirect to login
function on401(err: AxiosError) {
  if (err.response?.status === 401 && typeof window !== "undefined") {
    clearStoredToken();
    window.location.href = "/login";
  }
  return Promise.reject(err);
}
api.interceptors.response.use((r) => r, on401);
apiSlow.interceptors.response.use((r) => r, on401);

// ── Human-readable error extraction ──────────────────────────────────────────
// Call this in every catch block instead of err.response?.data?.detail
// It returns a useful message even for network/CORS failures where response is undefined.
export function getErrorMessage(err: any): string {
  if (!err) return "Unknown error";
  // Server responded with an error body
  if (err.response?.data?.detail) return String(err.response.data.detail);
  if (err.response?.data?.message) return String(err.response.data.message);
  // Network error (no response) = CORS blocked, backend unreachable, timeout
  if (err.code === "ECONNABORTED") return "Request timed out — backend may be overloaded";
  if (!err.response) {
    return `Cannot reach backend at ${API_URL}. Check that NEXT_PUBLIC_API_URL is set correctly and the backend is running.`;
  }
  if (err.response.status === 422) return "Invalid request format — check your input";
  if (err.response.status === 500) return "Backend error — check server logs";
  return err.message || "Request failed";
}

// ── Auth helpers ──────────────────────────────────────────────────────────────
export async function stepOneLogin(username: string, password: string) {
  const { data } = await api.post("/auth/login", { username, password });
  return data;
}

export async function stepTwoVerifyOTP(username: string, otp: string) {
  const { data } = await api.post("/auth/verify-otp", { username, otp });
  return data;
}

export function logout() {
  clearStoredToken();
  window.location.href = "/login";
}

export function getToken(): string | null {
  return getStoredToken();
}

export function isAuthenticated(): boolean {
  return !!getStoredToken();
}

export const fetcher = (url: string) =>
  api.get(url).then((r) => r.data);
