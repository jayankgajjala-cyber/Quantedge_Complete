import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 30000,
});

// ── Token helpers ─────────────────────────────────────────────────────────────
// Zustand persist stores under key "trading-auth" as JSON: { state: { token, username } }
// api.ts must read from the same place so JWT is attached to every request.

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("trading-auth");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.state?.token ?? null;
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

// ── Interceptors ──────────────────────────────────────────────────────────────

api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && typeof window !== "undefined") {
      clearStoredToken();
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

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
