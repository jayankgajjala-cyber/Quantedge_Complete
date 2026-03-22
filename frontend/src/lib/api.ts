import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 30000,
});

// Attach JWT to every request
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Redirect to /login on 401
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("access_token");
      localStorage.removeItem("username");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// ── Auth helpers ─────────────────────────────────────────────────────────────

export async function stepOneLogin(username: string, password: string) {
  const { data } = await api.post("/auth/login", { username, password });
  return data;
}

export async function stepTwoVerifyOTP(username: string, otp: string) {
  const { data } = await api.post("/auth/verify-otp", { username, otp });
  if (data.access_token) {
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("username", data.username);
  }
  return data;
}

export function logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("username");
  window.location.href = "/login";
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

// ── Typed fetch helpers (used by SWR) ───────────────────────────────────────

export const fetcher = (url: string) =>
  api.get(url).then((r) => r.data);
