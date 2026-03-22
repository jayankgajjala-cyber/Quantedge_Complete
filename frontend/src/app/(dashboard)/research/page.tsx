"use client";
import { useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  Newspaper, Search, AlertTriangle, TrendingUp, TrendingDown,
  Minus, Sparkles, BarChart3, RefreshCw, ExternalLink, Clock,
} from "lucide-react";
import { toast } from "sonner";
import { useResearch, useNews } from "@/hooks/useData";
import { cn, sentimentColor, timeAgo, fmtDate, regimeBadge } from "@/lib/utils";
import { Card, CardHeader, CardContent, Badge, Skeleton, Empty } from "@/components/ui";
import CandlestickChart from "@/components/charts/CandlestickChart";
import type { NewsArticle } from "@/types";

const NIFTY_50 = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","WIPRO",
  "AXISBANK","KOTAKBANK","LT","BAJFINANCE","MARUTI","TATAMOTORS","HCLTECH","SUNPHARMA",
];

function SentimentDot({ score }: { score: number | null }) {
  if (score == null) return <span className="w-2 h-2 rounded-full bg-muted inline-block" />;
  const color = score > 0.3 ? "bg-bull" : score < -0.3 ? "bg-bear" : "bg-gold";
  return <span className={cn("w-2 h-2 rounded-full inline-block", color)} />;
}

function ScoreBar({ score }: { score: number | null }) {
  if (score == null) return null;
  const pct = ((score + 1) / 2) * 100;
  const color = score > 0.3 ? "bg-bull" : score < -0.3 ? "bg-bear" : "bg-gold";
  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] text-muted-foreground font-mono w-10 text-right">{score.toFixed(3)}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden relative">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border" />
        <div className={cn("absolute top-0 h-full rounded-full", color)}
          style={{ left: score >= 0 ? "50%" : `${pct}%`, width: `${Math.abs(score) * 50}%` }} />
      </div>
    </div>
  );
}

export default function ResearchPage() {
  const router        = useRouter();
  const params        = useSearchParams();
  const [ticker, setTicker] = useState<string>(params.get("ticker") || "RELIANCE");
  const [input, setInput]   = useState(ticker);

  const { data: research, isLoading: resLoading, mutate: mutateRes } = useResearch(ticker);
  const { data: news,     isLoading: newsLoading } = useNews(ticker);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (!t) return;
    setTicker(t);
    router.push(`/research?ticker=${t}`, { scroll: false });
  }

  const sentScore = research?.avg_sentiment_score ?? 0;
  const sentLabel = sentScore > 0.3 ? "Positive" : sentScore < -0.3 ? "Negative" : "Neutral";
  const SentIcon  = sentScore > 0.3 ? TrendingUp : sentScore < -0.3 ? TrendingDown : Minus;

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-display font-bold text-xl">News Research</h1>
          <p className="text-muted-foreground text-xs mt-0.5">AI-powered sentiment · FinBERT + BART · 60-min cache</p>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative">
            <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Enter ticker…"
              className="bg-muted/50 border border-border rounded-xl pl-8 pr-4 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/30 transition-all w-36"
            />
          </div>
          <button type="submit"
            className="px-4 py-2 rounded-xl bg-primary/10 border border-primary/30 text-primary text-xs font-semibold hover:bg-primary/20 transition-all">
            Analyse
          </button>
        </form>
      </div>

      {/* Quick ticker pills */}
      <div className="flex gap-2 flex-wrap">
        {NIFTY_50.map((t) => (
          <button key={t}
            onClick={() => { setTicker(t); setInput(t); router.push(`/research?ticker=${t}`, { scroll: false }); }}
            className={cn(
              "px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-all",
              ticker === t
                ? "bg-primary/15 border-primary/40 text-primary"
                : "border-border text-muted-foreground hover:text-foreground hover:border-border/80 bg-muted/30"
            )}>
            {t}
          </button>
        ))}
      </div>

      {/* Insufficient coverage */}
      {research?.insufficient_coverage && (
        <div className="bg-gold/5 border border-gold/30 rounded-2xl p-4 flex gap-3">
          <AlertTriangle size={14} className="text-gold shrink-0 mt-0.5" />
          <p className="text-xs text-gold/80">{research.coverage_message}</p>
        </div>
      )}

      {/* Conflict warning */}
      {research?.conflict_detected && (
        <div className="bg-bear/5 border border-bear/30 rounded-2xl p-4 flex gap-3">
          <AlertTriangle size={14} className="text-bear shrink-0 mt-0.5" />
          <div>
            <p className="text-xs font-bold text-bear mb-1">[NEWS CONFLICT DETECTED]</p>
            <p className="text-[10px] text-bear/70">{research.conflict_detail}</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-5">
        {/* Left: Insight + News feed */}
        <div className="space-y-4">

          {/* Executive Insight Box */}
          <div className="bg-gradient-to-br from-primary/10 to-accent/5 border border-primary/20 rounded-2xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-6 h-6 rounded-lg bg-primary/20 flex items-center justify-center">
                <Sparkles size={12} className="text-primary" />
              </div>
              <span className="text-xs font-bold uppercase tracking-widest text-primary">
                AI Executive Insight — {ticker}
              </span>
              {research && (
                <button onClick={() => mutateRes()}
                  className="ml-auto text-muted-foreground hover:text-foreground transition-colors">
                  <RefreshCw size={11} />
                </button>
              )}
            </div>

            {resLoading ? (
              <div className="space-y-2">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-5 w-full" />)}</div>
            ) : research?.executive_summary?.length ? (
              <ul className="space-y-3">
                {research.executive_summary.map((bullet, i) => (
                  <li key={i} className="flex gap-3 text-sm leading-relaxed animate-fade-in"
                    style={{ animationDelay: `${i * 80}ms` }}>
                    <span className="text-primary font-bold shrink-0 mt-0.5">
                      {["①","②","③"][i] ?? "•"}
                    </span>
                    <span className="text-foreground/90">{bullet.replace(/^[•–]\s*/, "")}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">Generating AI summary…</p>
            )}

            {/* Forecast outlook */}
            {research?.forecast_outlook && (
              <div className="mt-4 pt-4 border-t border-primary/20">
                <div className="flex items-center gap-2">
                  <span className="text-[9px] uppercase tracking-widest text-muted-foreground">12–24 Month Outlook</span>
                  <span className={cn("text-[9px] font-bold px-2 py-0.5 rounded-full border",
                    research.forecast_direction === "BULLISH" ? "text-bull bg-bull/10 border-bull/20"
                    : research.forecast_direction === "BEARISH" ? "text-bear bg-bear/10 border-bear/20"
                    : "text-gold bg-gold/10 border-gold/20"
                  )}>
                    {research.forecast_direction}
                  </span>
                </div>
                <p className="text-xs text-foreground/80 mt-1.5">{research.forecast_outlook}</p>
              </div>
            )}
          </div>

          {/* News feed — latest-first */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Newspaper size={13} className="text-muted-foreground" />
              <span className="text-sm font-semibold">Latest News</span>
              <Badge variant="neutral" size="sm">{news?.length ?? 0} articles</Badge>
              <span className="ml-auto text-[9px] text-muted-foreground">Latest first ↑</span>
            </div>

            {newsLoading ? (
              <div className="space-y-2">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}</div>
            ) : !news?.length ? (
              <Empty icon={<Newspaper size={28} />} title="No articles found" description="Analysis will run automatically on first search" />
            ) : (
              <div className="space-y-2">
                {news.map((article: NewsArticle, i: number) => (
                  <div key={i}
                    className="bg-card border border-border rounded-xl p-3.5 hover:border-primary/20 transition-all animate-fade-in group"
                    style={{ animationDelay: `${i * 30}ms` }}>
                    <div className="flex items-start gap-3">
                      <SentimentDot score={article.sentiment_score} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-xs font-semibold text-foreground leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                            {article.title}
                          </p>
                          {article.url && (
                            <a href={article.url} target="_blank" rel="noopener noreferrer"
                              className="shrink-0 text-muted-foreground hover:text-primary transition-colors mt-0.5">
                              <ExternalLink size={10} />
                            </a>
                          )}
                        </div>
                        {article.description && (
                          <p className="text-[10px] text-muted-foreground mt-1 line-clamp-2">{article.description}</p>
                        )}
                        <div className="flex items-center gap-3 mt-2">
                          <span className={cn("text-[9px] font-semibold uppercase",
                            article.source_name === "NEWSAPI" ? "text-cyan" : "text-muted-foreground"
                          )}>{article.source_name.replace("_", " ")}</span>
                          <span className="text-[9px] text-muted-foreground flex items-center gap-1">
                            <Clock size={8} />{timeAgo(article.published_at)}
                          </span>
                          {article.sentiment_score != null && (
                            <div className="ml-auto w-24">
                              <ScoreBar score={article.sentiment_score} />
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Sentiment panel + chart */}
        <div className="space-y-4">
          {/* Sentiment summary */}
          <Card>
            <CardHeader>
              <span className="text-xs font-semibold flex items-center gap-1.5">
                <SentIcon size={12} className={sentimentColor(sentScore)} />
                Sentiment Analysis
              </span>
            </CardHeader>
            <CardContent>
              {resLoading ? (
                <div className="space-y-3">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-6 w-full" />)}</div>
              ) : research ? (
                <div className="space-y-4">
                  {/* Big score */}
                  <div className="text-center py-2">
                    <div className={cn("text-4xl font-display font-bold", sentimentColor(sentScore))}>
                      {sentScore >= 0 ? "+" : ""}{sentScore.toFixed(3)}
                    </div>
                    <div className="text-xs text-muted-foreground mt-1">{sentLabel} Sentiment</div>
                  </div>

                  {/* Vote bars */}
                  {[
                    { label: "Positive", count: research.positive_count, color: "bg-bull", total: research.articles_analysed },
                    { label: "Neutral",  count: research.neutral_count,  color: "bg-gold", total: research.articles_analysed },
                    { label: "Negative", count: research.negative_count, color: "bg-bear", total: research.articles_analysed },
                  ].map(({ label, count, color, total }) => (
                    <div key={label}>
                      <div className="flex justify-between text-[10px] mb-1">
                        <span className="text-muted-foreground">{label}</span>
                        <span className="font-mono font-semibold">{count}/{total}</span>
                      </div>
                      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                        <div className={cn("h-full rounded-full transition-all", color)}
                          style={{ width: total > 0 ? `${(count / total) * 100}%` : "0%" }} />
                      </div>
                    </div>
                  ))}

                  <div className="pt-2 border-t border-border space-y-2 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Articles analysed</span>
                      <span className="font-mono font-semibold">{research.articles_analysed}</span>
                    </div>
                    {research.sentiment_std_dev != null && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Divergence (σ)</span>
                        <span className={cn("font-mono font-semibold",
                          research.sentiment_std_dev > 0.8 ? "text-bear" : "text-foreground")}>
                          {research.sentiment_std_dev.toFixed(3)}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>

          {/* Mini chart */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <BarChart3 size={12} className="text-muted-foreground" />
              <span className="text-xs font-semibold">{ticker} · Chart</span>
            </div>
            <CandlestickChart ticker={ticker} showEMA showBB={false} showVolume={false} height={260} />
          </div>

          {/* Forecast card */}
          {research?.forecast_direction && (
            <Card>
              <CardContent>
                <div className="text-[9px] uppercase tracking-widest text-muted-foreground mb-2">Quantitative Forecast</div>
                <div className="space-y-2.5 text-xs">
                  {[
                    { label: "Direction",     value: research.forecast_direction,
                      className: research.forecast_direction === "BULLISH" ? "text-bull" : research.forecast_direction === "BEARISH" ? "text-bear" : "text-gold" },
                    { label: "Price Slope",   value: research.price_slope_annual ? `${research.price_slope_annual.toFixed(1)}%/yr` : "—", className: "" },
                    { label: "Revenue CAGR",  value: research.revenue_cagr ? `${research.revenue_cagr.toFixed(1)}%` : "—", className: "" },
                    { label: "Confidence",    value: research.forecast_confidence ? `${(research.forecast_confidence * 100).toFixed(0)}%` : "—", className: "" },
                  ].map(({ label, value, className }) => (
                    <div key={label} className="flex justify-between">
                      <span className="text-muted-foreground">{label}</span>
                      <span className={cn("font-mono font-semibold", className)}>{value}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
