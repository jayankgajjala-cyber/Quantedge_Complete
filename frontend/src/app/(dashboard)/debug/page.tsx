"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Check {
  label: string;
  status: "loading" | "ok" | "fail" | "warn";
  detail: string;
}

export default function DebugPage() {
  const [checks, setChecks] = useState<Check[]>([
    { label: "NEXT_PUBLIC_API_URL env var",    status: "loading", detail: "" },
    { label: "Backend reachable: GET /health", status: "loading", detail: "" },
    { label: "CORS headers present",           status: "loading", detail: "" },
    { label: "Auth token valid: GET /api/auth/me", status: "loading", detail: "" },
    { label: "Database: GET /api/trading/portfolio/holdings", status: "loading", detail: "" },
  ]);

  function set(i: number, status: Check["status"], detail: string) {
    setChecks((prev) =>
      prev.map((c, idx) => (idx === i ? { ...c, status, detail } : c))
    );
  }

  useEffect(() => {
    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    // 0 — env var
    if (!process.env.NEXT_PUBLIC_API_URL) {
      set(0, "warn", `Not set — defaulting to ${API_URL}`);
    } else {
      set(0, "ok", API_URL);
    }

    // 1 — health (no auth, plain fetch so we can catch CORS too)
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then((d) =>
        set(1, "ok", `status=${d.status}  db=${d.database}  scheduler=${d.scheduler}`)
      )
      .catch((e) =>
        set(1, "fail", `${e.message} — backend down, wrong URL, or CORS blocked`)
      );

    // 2 — CORS
    fetch(`${API_URL}/health`, { method: "OPTIONS" })
      .then((r) => {
        const h = r.headers.get("access-control-allow-origin");
        h
          ? set(2, "ok", `Access-Control-Allow-Origin: ${h}`)
          : set(2, "warn", "Header missing — set CORS_ORIGINS=* on backend");
      })
      .catch(() =>
        set(2, "fail", "OPTIONS preflight blocked — set CORS_ORIGINS=* on backend")
      );

    // 3 — auth/me via axios (uses stored JWT)
    api
      .get("/auth/me")
      .then((r) => set(3, "ok", `Logged in as: ${r.data.username}`))
      .catch((e) =>
        set(
          3,
          "fail",
          `HTTP ${e.response?.status ?? "Network Error"}: ${
            e.response?.data?.detail ?? e.message
          }`
        )
      );

    // 4 — holdings
    api
      .get("/trading/portfolio/holdings")
      .then((r) =>
        set(4, "ok", `${Array.isArray(r.data) ? r.data.length : "?"} holdings returned`)
      )
      .catch((e) =>
        set(
          4,
          "fail",
          `HTTP ${e.response?.status ?? "Network Error"}: ${
            e.response?.data?.detail ?? e.message
          }`
        )
      );
  }, []);

  const icon = (s: Check["status"]) =>
    ({ loading: "⏳", ok: "✅", warn: "⚠️", fail: "❌" }[s]);

  const colour = (s: Check["status"]) =>
    s === "ok"      ? "text-bull"
    : s === "fail"  ? "text-bear"
    : s === "warn"  ? "text-gold"
    : "text-muted-foreground animate-pulse";

  return (
    <div className="max-w-2xl space-y-5 animate-fade-in">
      <div>
        <h1 className="font-display font-bold text-xl">System Diagnostics</h1>
        <p className="text-muted-foreground text-xs mt-1">
          Run this page to identify exactly why buttons are not working. Each
          check is independent and shows the real error.
        </p>
      </div>

      <div className="bg-card border border-border rounded-2xl overflow-hidden divide-y divide-border/40">
        {checks.map((c, i) => (
          <div key={i} className="flex items-start gap-4 px-5 py-4">
            <span className="text-base shrink-0 mt-0.5 w-5 text-center">
              {icon(c.status)}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-foreground">{c.label}</div>
              <div className={cn("text-xs mt-0.5 font-mono break-all leading-relaxed", colour(c.status))}>
                {c.status === "loading" ? "checking…" : c.detail || "—"}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-muted/30 border border-border rounded-2xl p-5 space-y-2.5 text-xs leading-relaxed">
        <p className="font-bold text-sm">How to fix each failure:</p>
        <p>
          <span className="text-bear">❌ Backend reachable</span> — Backend is not
          running, or <code className="bg-muted px-1 rounded">NEXT_PUBLIC_API_URL</code> is
          wrong/missing in your frontend host.
        </p>
        <p>
          <span className="text-bear">❌ CORS</span> — Add{" "}
          <code className="bg-muted px-1 rounded">CORS_ORIGINS=*</code> to your backend
          host environment variables and redeploy.
        </p>
        <p>
          <span className="text-gold">⚠️ NEXT_PUBLIC_API_URL not set</span> — Add{" "}
          <code className="bg-muted px-1 rounded">
            NEXT_PUBLIC_API_URL=https://your-backend.onrender.com
          </code>{" "}
          to your frontend host env vars (Vercel / Netlify / etc.) and redeploy the
          frontend.
        </p>
        <p>
          <span className="text-bear">❌ Auth token</span> — Log out and log back
          in. If backend was restarted without a fixed JWT_SECRET_KEY, all tokens are
          invalid.
        </p>
      </div>
    </div>
  );
}
