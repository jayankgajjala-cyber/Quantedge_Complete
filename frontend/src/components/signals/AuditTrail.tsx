"use client";
import { cn } from "@/lib/utils";
import { CheckCircle2, XCircle, AlertCircle, Activity, Globe2, Newspaper, BarChart3 } from "lucide-react";

interface AuditTrailProps {
  confirmations: {
    technical_signal:    string;
    tradingview_summary: string;
    moneycontrol_mood:   string;
    news_sentiment:      string;
    sentiment_override:  boolean;
    macro_available:     boolean;
    macro_risk_flags:    string[];
    macro_notes:         string[];
    dxy?:                number | null;
    us_10y_yield?:       number | null;
    brent_crude?:        number | null;
    sl_multiplier?:      number;
    pos_size_factor?:    number;
    bias_warning:        boolean;
  };
}

const DATA_UNAVAILABLE = "DATA_UNAVAILABLE";

function SourcePill({
  icon: Icon, label, value, confirmed, unavailable,
}: {
  icon: React.ElementType; label: string; value: string;
  confirmed?: boolean; unavailable?: boolean;
}) {
  return (
    <div className={cn(
      "flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-[10px]",
      unavailable
        ? "bg-muted/30 border-border text-muted-foreground/60"
        : confirmed
        ? "bg-bull/10 border-bull/20 text-bull"
        : "bg-muted/40 border-border text-muted-foreground"
    )}>
      <Icon size={10} className="shrink-0" />
      <span className="font-semibold">{label}</span>
      <span className={cn("opacity-70", unavailable && "italic")}>
        {unavailable ? "N/A" : value}
      </span>
      {!unavailable && confirmed && <CheckCircle2 size={9} className="text-bull" />}
      {!unavailable && !confirmed && value !== "NEUTRAL" && value !== "HOLD" &&
        <AlertCircle size={9} className="text-gold" />}
    </div>
  );
}

export default function AuditTrail({ confirmations: c }: AuditTrailProps) {
  if (!c) return null;

  const tvUnavail = c.tradingview_summary === DATA_UNAVAILABLE;
  const mcUnavail = c.moneycontrol_mood   === DATA_UNAVAILABLE;

  const tvConfirmed = !tvUnavail && ["STRONG_BUY","BUY"].includes(c.tradingview_summary);
  const mcConfirmed = !mcUnavail && ["BULLISH","NEUTRAL"].includes(c.moneycontrol_mood);
  const sentConfirmed = !["NEGATIVE"].includes(c.news_sentiment?.toUpperCase?.() || "");

  return (
    <div className="space-y-3 pt-3 border-t border-border/40">
      <p className="text-[9px] uppercase tracking-widest text-muted-foreground font-semibold">
        Source Confirmations
      </p>

      {/* Source pills */}
      <div className="flex flex-wrap gap-2">
        <SourcePill
          icon={BarChart3}
          label="Technical"
          value={c.technical_signal}
          confirmed={["BUY","SELL"].includes(c.technical_signal)}
        />
        <SourcePill
          icon={Activity}
          label="TradingView"
          value={c.tradingview_summary}
          confirmed={tvConfirmed}
          unavailable={tvUnavail}
        />
        <SourcePill
          icon={Newspaper}
          label="Moneycontrol"
          value={c.moneycontrol_mood}
          confirmed={mcConfirmed}
          unavailable={mcUnavail}
        />
        <SourcePill
          icon={Globe2}
          label="FinBERT"
          value={c.news_sentiment}
          confirmed={sentConfirmed}
        />
      </div>

      {/* Macro context */}
      {c.macro_available && (
        <div className="bg-muted/20 rounded-xl p-2.5 border border-border/40 space-y-1.5">
          <p className="text-[9px] uppercase tracking-widest text-muted-foreground font-semibold">
            Macro Context (Investing.com)
          </p>
          <div className="flex flex-wrap gap-3 text-[10px]">
            {c.dxy != null && (
              <span className={cn("font-mono", (c.dxy ?? 0) >= 106 ? "text-bear" : "text-foreground/70")}>
                DXY: {c.dxy.toFixed(1)}{(c.dxy ?? 0) >= 106 && " ▲ HIGH"}
              </span>
            )}
            {c.us_10y_yield != null && (
              <span className={cn("font-mono", (c.us_10y_yield ?? 0) >= 5 ? "text-bear" : "text-foreground/70")}>
                US10Y: {c.us_10y_yield.toFixed(2)}%{(c.us_10y_yield ?? 0) >= 5 && " ▲ HIGH"}
              </span>
            )}
            {c.brent_crude != null && (
              <span className={cn("font-mono", (c.brent_crude ?? 0) >= 95 ? "text-bear" : "text-foreground/70")}>
                Brent: ${c.brent_crude.toFixed(1)}{(c.brent_crude ?? 0) >= 95 && " ▲ RISK"}
              </span>
            )}
            {c.sl_multiplier != null && (
              <span className="text-muted-foreground">
                SL mult: <span className="font-mono">{c.sl_multiplier.toFixed(3)}×ATR</span>
              </span>
            )}
            {c.pos_size_factor != null && c.pos_size_factor < 1 && (
              <span className="text-gold">
                Pos size: <span className="font-mono">{(c.pos_size_factor * 100).toFixed(0)}%</span>
              </span>
            )}
          </div>
          {c.macro_risk_flags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {c.macro_risk_flags.map(f => (
                <span key={f} className="text-[9px] bg-bear/10 border border-bear/20 text-bear px-1.5 py-0.5 rounded-full">
                  {f}
                </span>
              ))}
            </div>
          )}
          {c.macro_notes.map((note, i) => (
            <p key={i} className="text-[9px] text-muted-foreground leading-relaxed">{note}</p>
          ))}
        </div>
      )}

      {!c.macro_available && (
        <p className="text-[9px] text-muted-foreground/50 italic">
          Macro data (Investing.com): DATA_UNAVAILABLE — default risk parameters applied.
        </p>
      )}

      {/* Override / bias notices */}
      {c.sentiment_override && (
        <div className="flex items-center gap-1.5 text-[10px] text-gold">
          <AlertCircle size={10} />
          Signal was overridden by sentiment analysis
        </div>
      )}
      {c.bias_warning && (
        <div className="flex items-center gap-1.5 text-[10px] text-gold">
          <AlertCircle size={10} />
          Regime bias detected — confidence reduced
        </div>
      )}
    </div>
  );
}
