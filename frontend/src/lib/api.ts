/**
 * frontend/src/lib/api.ts — v3.0 (Production-Ready)
 *
 * CHANGES:
 * ─────────────────────────────────────────────────────────────────────────────
 * FIX 1 — STRUCTURED ERROR HANDLING:
 *   The backend now returns structured JSON errors:
 *     { error: "OperationalError", message: "...", detail: "...", request_id: "..." }
 *   getErrorMessage() now extracts all three fields and includes the request_id
 *   so Railway logs can be searched by that ID.
 *
 * FIX 2 — TOAST NOTIFICATIONS:
 *   withToast() wraps any API call and automatically shows a Sonner toast:
 *     - On success: green toast with optional custom message
 *     - On error:   red toast with the structured error message + request_id
 *   Usage: await withToast(api.post("/dashboard/scan-now"), "Scan complete!")
 *
 * FIX 3 — HEALTH CHECK:
 *   checkHealth() hits /api/health and returns a typed HealthStatus object.
 *   Used by the Settings page to show live connection status.
 *
 * FIX 4 — REQUEST ID HEADER:
 *   All responses now include X-Request-ID. Axios interceptors extract it and
 *   attach it to error objects so it shows up in toast notifications.
 *
 * FIX 5 — 404 DIAGNOSIS:
 *   Network errors now include a diagnosis checklist in the error message,
 *   listing the three most common causes of 404s in production.
 * ─────────────────────────────────────────────────────────────────────────────
 */
import axios, { AxiosError, AxiosResponse } from "axios";
import { toast } from "sonner";

// ── Base URL ──────────────────────────────────────────────────────────────────
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Axios instances ───────────────────────────────────────────────────────────
// api      — 30s  — all standard reads + mutations
// apiSlow  — 120s — backtest, scan-now, research (heavy ML operations)

export const api = axios.create({
  baseURL: `${API_BASE}/api`,
  timeout: 30_000,
});

export const apiSlow = axios.create({
  baseURL: `${API_BASE}/api`,
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


// ── Interceptors ──────────────────────────────────────────────────────────────

function attachAuth(config: any) {
  const token = getStoredToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
}

function handleResponse(response: AxiosResponse): AxiosResponse {
  // Extract X-Request-ID from response headers and attach to response.data
  // so getErrorMessage() and withToast() can surface it in error toasts
  const requestId = response.headers?.["x-request-id"];
  if (requestId && response.data && typeof response.data === "object") {
    response.data._requestId = requestId;
  }
  return response;
}

function on401(err: AxiosError): Promise<never> {
  if (err.response?.status === 401 && typeof window !== "undefined") {
    clearStoredToken();
    window.location.href = "/login";
  }
  return Promise.reject(err);
}

// FIX 4: Extract request_id from error response headers
function enrichError(err: AxiosError): Promise<never> {
  const requestId = err.response?.headers?.["x-request-id"];
  if (requestId && err.response?.data && typeof err.response.data === "object") {
    (err.response.data as any)._requestId = requestId;
  }
  return Promise.reject(err);
}

api.interceptors.request.use(attachAuth);
apiSlow.interceptors.request.use(attachAuth);
api.interceptors.response.use(handleResponse, (e) => enrichError(e).catch(on401));
apiSlow.interceptors.response.use(handleResponse, (e) => enrichError(e).catch(on401));


// ── FIX 1: Structured error extraction ───────────────────────────────────────

export interface BackendError {
  error:      string;   // exception class name, e.g. "OperationalError"
  message:    string;   // user-friendly hint from the global exception handler
  detail:     string;   // raw exception message
  path:       string;
  request_id: string;
}

/**
 * Extracts a human-readable error string from any axios error.
 *
 * Priority:
 *   1. Structured backend error → { error, message, detail, request_id }
 *   2. FastAPI validation error → detail array
 *   3. FastAPI HTTPException    → detail string
 *   4. Network/timeout/CORS    → diagnosis checklist
 *   5. HTTP status fallback
 */
export function getErrorMessage(err: any): string {
  if (!err) return "Unknown error";

  const data = err.response?.data;
  const requestId = data?._requestId ?? data?.request_id;
  const ridSuffix = requestId ? ` [ID: ${requestId}]` : "";

  // FIX 1: Structured backend error (from global_exception_handler)
  if (data?.error && data?.message) {
    const detail = data.detail ? ` — ${data.detail}` : "";
    return `${data.message}${detail}${ridSuffix}`;
  }

  // FastAPI validation error (422 Unprocessable Entity)
  if (data?.detail) {
    const d = data.detail;
    if (Array.isArray(d)) {
      return d.map((e: any) => `${e.loc?.join(".")}: ${e.msg}`).join("; ") + ridSuffix;
    }
    if (typeof d === "string") return `${d}${ridSuffix}`;
    return `${String(d)}${ridSuffix}`;
  }

  if (data?.message) return `${String(data.message)}${ridSuffix}`;

  // Timeout
  if (err.code === "ECONNABORTED") {
    return (
      "Request timed out — the backend is taking too long.\n" +
      "Tip: Backtest and scan-now operations take 30–90 seconds. " +
      "If this keeps happening, check Railway logs for the backend process."
    );
  }

  // FIX 5: Network error with diagnosis checklist
  if (!err.response) {
    return (
      `Cannot reach backend at ${API_BASE}.\n` +
      "Check these in order:\n" +
      "  1. NEXT_PUBLIC_API_URL is set in Vercel env vars (e.g. https://your-app.railway.app)\n" +
      "  2. CORS_ORIGINS is set in Railway env vars (e.g. https://your-app.vercel.app)\n" +
      "  3. Railway backend service is Running (not Crashed/Sleeping)"
    );
  }

  // HTTP status fallbacks
  switch (err.response.status) {
    case 400: return `Bad request: ${data?.detail ?? "check your input"}${ridSuffix}`;
    case 401: return `Authentication required — please log in again${ridSuffix}`;
    case 403: return `Access denied${ridSuffix}`;
    case 404: return (
      `404 Not Found: ${err.config?.url ?? "unknown endpoint"}\n` +
      "  → Check that NEXT_PUBLIC_API_URL points to the correct Railway URL\n" +
      "  → Confirm the Railway service is deployed and /api/health returns 200"
    );
    case 422: return `Validation error — check your input data${ridSuffix}`;
    case 429: return `Too many requests — please wait a moment${ridSuffix}`;
    case 500: return (
      `Internal server error${ridSuffix}\n` +
      "  → Check Railway logs for the traceback\n" +
      `  → Search for: ${requestId ?? "request_id"}`
    );
    case 502: return `Backend unreachable — Railway service may be starting up. Retry in 30s${ridSuffix}`;
    case 503: return `Service unavailable — backend is overloaded${ridSuffix}`;
    default:  return err.message || `HTTP ${err.response.status} error${ridSuffix}`;
  }
}


// ── FIX 2: withToast wrapper ──────────────────────────────────────────────────

interface ToastOptions {
  successMessage?: string;
  successDescription?: string;
  errorTitle?: string;
  /** If true, no success toast is shown (for silent background operations) */
  silent?: boolean;
}

/**
 * Wraps any API promise and shows Sonner toasts for success and error.
 *
 * Usage:
 *   const data = await withToast(
 *     api.post("/dashboard/scan-now"),
 *     { successMessage: "Scan complete!", errorTitle: "Scan failed" }
 *   );
 */
export async function withToast<T>(
  promise: Promise<AxiosResponse<T>>,
  options: ToastOptions = {},
): Promise<T> {
  try {
    const response = await promise;
    if (!options.silent) {
      toast.success(options.successMessage ?? "Done", {
        description: options.successDescription,
      });
    }
    return response.data;
  } catch (err: any) {
    const message = getErrorMessage(err);
    const requestId = err.response?.data?._requestId ?? err.response?.data?.request_id;
    toast.error(options.errorTitle ?? "Error", {
      description: message,
      action: requestId
        ? {
            label: "Copy ID",
            onClick: () => navigator.clipboard.writeText(requestId),
          }
        : undefined,
      duration: 8000,  // longer for errors so user can read them
    });
    throw err;  // re-throw so callers can handle locally if needed
  }
}


// ── FIX 3: Health check ───────────────────────────────────────────────────────

export interface HealthStatus {
  status:    "ok" | "degraded";
  version:   string;
  timestamp: string;
  database: {
    status:     "ok" | "error";
    driver:     string;
    latency_ms: number | null;
    detail:     string | null;
  };
  scheduler: {
    status:      "running" | "stopped" | "error";
    active_jobs: number;
    timezone:    string;
  };
  cache: {
    parquet_dir: string;
    writable:    boolean;
  };
  cors: {
    mode:    "wildcard" | "explicit";
    origins: string[];
  };
}

/**
 * Check the backend health endpoint.
 * Returns null if the backend is unreachable (network error).
 *
 * Usage in Settings page:
 *   const health = await checkHealth();
 *   if (!health) → "Backend unreachable"
 *   if (health.database.status === "error") → "DB connection failed"
 */
export async function checkHealth(): Promise<HealthStatus | null> {
  try {
    // Use a bare axios call (not `api`) so it doesn't go through /api prefix
    const { data } = await axios.get<HealthStatus>(`${API_BASE}/api/health`, {
      timeout: 10_000,
    });
    return data;
  } catch {
    return null;
  }
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

export function getToken():       string | null { return getStoredToken(); }
export function isAuthenticated():boolean       { return !!getStoredToken(); }

export const fetcher = (url: string) => api.get(url).then((r) => r.data);
