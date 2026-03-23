import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Default client — 30s timeout for normal reads/mutations
export const api = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 30_000,
});

// Long-running operations: backtest fetches 10yr data + runs 8 strategies per ticker.
// 30s is not enough. Use 120s for these calls only.
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

// 401 on either client → clear token and redirect to login
function on401(err: any) {
  if (err.response?.status === 401 && typeof window !== "undefined") {
    clearStoredToken();
    window.location.href = "/login";
  }
  return Promise.reject(err);
}
api.interceptors.response.use((r) => r, on401);
apiSlow.interceptors.response.use((r) => r, on401);

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
