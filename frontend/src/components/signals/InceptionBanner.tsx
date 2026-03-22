"use client";
import { AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

interface InceptionBannerProps {
  quality:         string;
  qualityMessage:  string;
  isInception:     boolean;
  inceptionDate?:  string;
  className?:      string;
}

/**
 * Displays mandatory UI banners for tickers with < 10 years of data.
 *
 * SUFFICIENT    → no banner (returns null)
 * INSUFFICIENT  → amber info banner: "Backtesting from inception [Date] only..."
 * LOW_CONFIDENCE → red warning banner: same + "LOW CONFIDENCE (<24 months)"
 */
export default function InceptionBanner({
  quality, qualityMessage, isInception, className,
}: InceptionBannerProps) {
  if (!isInception || quality === "SUFFICIENT") return null;

  const isLow = quality === "LOW_CONFIDENCE";

  return (
    <div className={cn(
      "flex gap-2.5 rounded-xl border p-3",
      isLow
        ? "bg-bear/5 border-bear/25"
        : "bg-gold/5 border-gold/25",
      className
    )}>
      {isLow ? (
        <AlertTriangle size={14} className="text-bear shrink-0 mt-0.5" />
      ) : (
        <Info size={14} className="text-gold shrink-0 mt-0.5" />
      )}
      <div className="space-y-0.5">
        {isLow && (
          <p className="text-[10px] font-bold uppercase tracking-widest text-bear">
            Low Confidence
          </p>
        )}
        <p className={cn(
          "text-[11px] leading-relaxed",
          isLow ? "text-bear/80" : "text-gold/80"
        )}>
          {qualityMessage}
        </p>
      </div>
    </div>
  );
}
