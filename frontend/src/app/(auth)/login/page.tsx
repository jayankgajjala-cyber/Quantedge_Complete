"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Lock, User, Shield, ArrowRight, Loader2, Eye, EyeOff } from "lucide-react";
import { stepOneLogin, stepTwoVerifyOTP, getErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/store";
import { cn } from "@/lib/utils";

type Step = "password" | "otp";

export default function LoginPage() {
  const router    = useRouter();
  const setToken  = useAuthStore((s) => s.setToken);
  const [step, setStep]           = useState<Step>("password");
  const [username, setUsername]   = useState("Jayank8294");
  const [password, setPassword]   = useState("");
  const [otp, setOtp]             = useState("");
  const [loading, setLoading]     = useState(false);
  const [showPw, setShowPw]       = useState(false);
  const [emailHint, setEmailHint] = useState("");

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await stepOneLogin(username, password);
      setEmailHint(res.email_hint);
      setStep("otp");
      toast.success("OTP sent!", { description: `Check ${res.email_hint}` });
    } catch (err: any) {
      toast.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleOTPSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await stepTwoVerifyOTP(username, otp);
      setToken(res.access_token, res.username);
      toast.success("Welcome back, " + res.username);
      router.replace("/");
    } catch (err: any) {
      toast.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4 overflow-hidden relative">
      <div className="absolute inset-0 scanline pointer-events-none" />
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] rounded-full bg-primary/5 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-0 right-0 w-[400px] h-[300px] rounded-full bg-accent/5 blur-[100px] pointer-events-none" />

      <div className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage: `linear-gradient(rgba(0,196,125,0.04) 1px, transparent 1px),
                            linear-gradient(90deg, rgba(0,196,125,0.04) 1px, transparent 1px)`,
          backgroundSize: "60px 60px",
        }} />

      <div className="relative w-full max-w-[420px] animate-fade-in">
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-lg bg-primary/20 border border-primary/30 flex items-center justify-center">
              <span className="text-primary font-display font-bold text-sm">Q</span>
            </div>
            <span className="font-display font-bold text-xl tracking-tight">QUANTEDGE</span>
          </div>
          <p className="text-muted-foreground text-sm">Institutional Trading Intelligence</p>
        </div>

        <div className="glass rounded-2xl p-8">
          <div className="flex items-center gap-3 mb-8">
            {(["password", "otp"] as Step[]).map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <div className={cn(
                  "w-7 h-7 rounded-full border flex items-center justify-center text-xs font-bold transition-all",
                  step === s || (s === "password" && step === "otp")
                    ? "border-primary bg-primary/20 text-primary"
                    : "border-border text-muted-foreground"
                )}>
                  {s === "password" && step === "otp" ? "✓" : i + 1}
                </div>
                <span className={cn(
                  "text-xs font-medium capitalize hidden sm:block",
                  step === s ? "text-foreground" : "text-muted-foreground"
                )}>
                  {s === "password" ? "Credentials" : "Verify OTP"}
                </span>
                {i === 0 && (
                  <div className={cn(
                    "w-8 h-px mx-1",
                    step === "otp" ? "bg-primary" : "bg-border"
                  )} />
                )}
              </div>
            ))}
          </div>

          {step === "password" ? (
            <form onSubmit={handlePasswordSubmit} className="space-y-5">
              <div>
                <label className="text-xs text-muted-foreground font-medium uppercase tracking-widest block mb-2">
                  Username
                </label>
                <div className="relative">
                  <User size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full bg-muted/50 border border-border rounded-xl pl-9 pr-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50 transition-all"
                    placeholder="Enter username"
                    autoComplete="username"
                    required
                  />
                </div>
              </div>

              <div>
                <label className="text-xs text-muted-foreground font-medium uppercase tracking-widest block mb-2">
                  Password
                </label>
                <div className="relative">
                  <Lock size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    type={showPw ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-muted/50 border border-border rounded-xl pl-9 pr-10 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50 transition-all"
                    placeholder="Enter password"
                    autoComplete="current-password"
                    required
                  />
                  <button type="button" onClick={() => setShowPw(!showPw)}
                    className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors">
                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-primary/90 hover:bg-primary text-black font-bold py-3 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-60 text-sm">
                {loading ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
                {loading ? "Verifying..." : "Continue"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleOTPSubmit} className="space-y-5">
              <div className="text-center py-2">
                <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-primary/10 border border-primary/20 mb-3">
                  <Shield size={20} className="text-primary" />
                </div>
                <p className="text-sm text-foreground font-medium">OTP sent to</p>
                <p className="text-primary text-sm font-mono mt-1">{emailHint}</p>
                <p className="text-muted-foreground text-xs mt-1">Valid for 5 minutes</p>
              </div>

              <div>
                <label className="text-xs text-muted-foreground font-medium uppercase tracking-widest block mb-2">
                  6-Digit OTP
                </label>
                <input
                  value={otp}
                  onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  className="w-full bg-muted/50 border border-border rounded-xl px-4 py-3 text-center text-2xl font-mono tracking-[0.5em] focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50 transition-all"
                  placeholder="••••••"
                  inputMode="numeric"
                  maxLength={6}
                  required
                />
              </div>

              <button type="submit" disabled={loading || otp.length !== 6}
                className="w-full bg-primary/90 hover:bg-primary text-black font-bold py-3 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-60 text-sm">
                {loading ? <Loader2 size={15} className="animate-spin" /> : <Shield size={15} />}
                {loading ? "Verifying OTP..." : "Authenticate"}
              </button>

              <button type="button" onClick={() => setStep("password")}
                className="w-full text-muted-foreground hover:text-foreground text-xs transition-colors text-center">
                ← Back to password
              </button>
            </form>
          )}
        </div>

        <p className="text-center text-muted-foreground text-xs mt-6">
          Secured with bcrypt + OTP two-factor authentication
        </p>
      </div>
    </div>
  );
}
