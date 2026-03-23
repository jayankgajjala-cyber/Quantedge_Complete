"use client";
import { ChevronDown, ChevronUp, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { cn, signalBg, fmt, regimeBadge, timeAgo } from "@/lib/utils";
import { ConfidenceBar } from "@/components/ui";
import AuditTrail from "@/components/signals/AuditTrail";
import type { FinalSignal } from "@/types";

interface SignalCardProps {
  signal: FinalSignal;
  onSelectTicker?: (ticker: string) => void;
}

export default function SignalCard({ signal, onSelectTicker }: SignalCardProps) {
  const [expanded, setExpanded] = useState(false);
  const regime = regimeBadge(signal.regime);

  return (
    <div className={cn(
      "bg-card border rounded-2xl overflow-hidden transition-all hover:border-primary/20",
      signal.bias_warning ? "border-gold/30" : "border-border",
    )}>
      <div className="p-4">
        <div className="flex items-start gap-3">
          <button
            onClick={() => onSelectTicker?.(signal.ticker)}
            className="w-10 h-10 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0 hover:bg-primary/20 transition-colors">
            <span className="text-primary text-xs font-bold">{signal.ticker.slice(0, 2)}</span>
          </button>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-display font-bold text-sm">{signal.ticker}</span>
              <span className={cn("text-[10px] font-bold px-2.5 py-0.5 rounded-full border", signalBg(signal.signal))}>
                {signal.signal}
              </span>
              {signal.agreement_bonus != null && signal.agreement_bonus > 0 && (
                <span className="text-[9px] bg-primary/10 text-primary border border-primary/20 px-1.5 py-0.5 rounded-full font-bold">
                  +{signal.agreement_bonus.toFixed(0)} AGREE
                </span>
              )}
              {signal.sentiment_override && (
                <span className="text-[9px] bg-gold/10 text-gold border border-gold/20 px-1.5 py-0.5 rounded-full font-bold">
                  SENTIMENT OVERRIDE
                </span>
              )}
            </div>

            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className={cn("text-[9px] px-2 py-0.5 rounded-full border font-medium", regime.color)}>
                {regime.icon} {regime.label}
              </span>
              <span className="text-[9px] text-muted-foreground">{signal.selected_strategy}</span>
            </div>

            <ConfidenceBar value={signal.confidence} className="mt-2.5 max-w-[180px]" />
          </div>

          {signal.entry_price && (
            <div className="text-right shrink-0 hidden sm:block">
              <div className="text-xs font-mono font-bold">₹{fmt(signal.entry_price)}</div>
              {signal.risk_reward_ratio && (
                <div className="text-[9px] text-muted-foreground mt-0.5">R:R {signal.risk_reward_ratio.toFixed(1)}</div>
              )}
            </div>
          )}

          <button onClick={() => setExpanded(!expanded)}
            className="text-muted-foreground hover:text-foreground transition-colors ml-1">
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>

        <div className="flex gap-4 mt-3 pt-3 border-t border-border/40 flex-wrap">
          {signal.adx != null && (
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-widest">ADX</div>
              <div className={cn("text-xs font-mono font-bold", signal.adx > 25 ? "text-bull" : "text-gold")}>
                {signal.adx.toFixed(1)}
              </div>
            </div>
          )}
          {signal.rsi != null && (
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-widest">RSI</div>
              <div className={cn("text-xs font-mono font-bold",
                signal.rsi > 70 ? "text-bear" : signal.rsi < 30 ? "text-bull" : "text-foreground")}>
                {signal.rsi.toFixed(1)}
              </div>
            </div>
          )}
          {signal.volume_ratio != null && (
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-widest">Vol×</div>
              <div className={cn("text-xs font-mono font-bold",
                signal.volume_ratio >= 1.5 ? "text-bull" : "text-muted-foreground")}>
                {signal.volume_ratio.toFixed(2)}x
              </div>
            </div>
          )}
          {signal.agreeing_strategies != null && (
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-widest">Agree</div>
              <div className="text-xs font-mono font-bold text-primary">
                {signal.agreeing_strategies}/{signal.total_strategies_run ?? "?"}
              </div>
            </div>
          )}
          {signal.sentiment_score != null && (
            <div className="text-center">
              <div className="text-[9px] text-muted-foreground uppercase tracking-widest">Sentiment</div>
              <div className={cn("text-xs font-mono font-bold",
                signal.sentiment_score > 0.3 ? "text-bull" : signal.sentiment_score < -0.3 ? "text-bear" : "text-gold")}>
                {signal.sentiment_score >= 0 ? "+" : ""}{signal.sentiment_score.toFixed(2)}
              </div>
            </div>
          )}
          <div className="ml-auto text-[9px] text-muted-foreground self-end">
            {timeAgo(signal.generated_at)}
          </div>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 border-t border-border/40 pt-3 space-y-3 animate-fade-in">
          {signal.entry_price && (
            <div className="grid grid-cols-3 gap-2">
              {[
                { label: "Entry",  value: signal.entry_price, color: "text-foreground" },
                { label: "SL",     value: signal.stop_loss,   color: "text-bear" },
                { label: "Target", value: signal.target_1,    color: "text-bull" },
              ].map(({ label, value, color }) => (
                <div key={label} className="bg-muted/40 rounded-xl p-2.5 text-center border border-border/60">
                  <div className="text-[9px] text-muted-foreground uppercase tracking-widest mb-1">{label}</div>
                  <div className={cn("text-xs font-mono font-bold", color)}>
                    {value != null ? `₹${fmt(value)}` : "—"}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="bg-muted/30 rounded-xl p-3 border border-border/40">
            <p className="text-[10px] text-muted-foreground leading-relaxed">{signal.reason}</p>
          </div>

          {signal.sentiment_override && signal.original_signal && (
            <div className="bg-gold/5 border border-gold/20 rounded-xl p-3 flex gap-2">
              <ShieldAlert size={13} className="text-gold shrink-0 mt-0.5" />
              <p className="text-[10px] text-gold/80 leading-relaxed">
                Signal overridden by FinBERT sentiment: original was{" "}
                <span className="font-bold">{signal.original_signal}</span>
                {signal.sentiment_score != null && ` (score: ${signal.sentiment_score.toFixed(3)})`}
              </p>
            </div>
          )}

          {signal.bias_warning && (
            <div className="bg-gold/5 border border-gold/20 rounded-xl p-3 flex gap-2">
              <ShieldAlert size={13} className="text-gold shrink-0 mt-0.5" />
              <p className="text-[10px] text-gold/80 leading-relaxed">{signal.bias_message}</p>
            </div>
          )}

          {signal.source_confirmations && (
            <AuditTrail confirmations={signal.source_confirmations} />
          )}
        </div>
      )}
    </div>
  );
}
