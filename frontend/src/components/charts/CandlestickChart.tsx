"use client";
import { useEffect, useRef, useCallback } from "react";
import { createChart, ColorType, CrosshairMode, LineStyle } from "lightweight-charts";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { useOHLCV } from "@/hooks/useData";
import {UTCTimestamp } from "lightweight-charts";

interface CandlestickChartProps {
  ticker: string;
  showEMA?: boolean;
  showBB?: boolean;
  showVolume?: boolean;
  height?: number;
}

const COLORS = {
  bg:          "#0d1117",
  grid:        "rgba(255,255,255,0.04)",
  border:      "rgba(255,255,255,0.08)",
  text:        "#6b7280",
  bullCandle:  "#00c47d",
  bearCandle:  "#ff4757",
  bullWick:    "#00c47d",
  bearWick:    "#ff4757",
  ema50:       "#00d4ff",
  ema200:      "#f0b429",
  bbUpper:     "rgba(99,102,241,0.7)",
  bbLower:     "rgba(99,102,241,0.7)",
  bbMid:       "rgba(99,102,241,0.3)",
  volBull:     "rgba(0,196,125,0.4)",
  volBear:     "rgba(255,71,87,0.4)",
  crosshair:   "rgba(255,255,255,0.2)",
};

function calcEMA(data: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const result: (number | null)[] = new Array(data.length).fill(null);
  let ema = data.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result[period - 1] = ema;
  for (let i = period; i < data.length; i++) {
    ema = data[i] * k + ema * (1 - k);
    result[i] = ema;
  }
  return result;
}

function calcBB(data: number[], period = 20, mult = 2) {
  const upper: (number | null)[] = new Array(data.length).fill(null);
  const lower: (number | null)[] = new Array(data.length).fill(null);
  const mid:   (number | null)[] = new Array(data.length).fill(null);
  for (let i = period - 1; i < data.length; i++) {
    const slice = data.slice(i - period + 1, i + 1);
    const mean  = slice.reduce((a, b) => a + b, 0) / period;
    const std   = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period);
    mid[i]   = mean;
    upper[i] = mean + mult * std;
    lower[i] = mean - mult * std;
  }
  return { upper, lower, mid };
}

export default function CandlestickChart({
  ticker, showEMA = true, showBB = true, showVolume = true, height = 480,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const cleanupRef   = useRef<(() => void) | null>(null);
  const { data: rawBars, isLoading } = useOHLCV(ticker, "daily", 500);

  const buildChart = useCallback(() => {
    if (!containerRef.current || !rawBars?.length) return;

    // Cleanup previous
    if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: showVolume ? height : height - 80,
      layout: {
        background:    { type: ColorType.Solid, color: COLORS.bg },
        textColor:     COLORS.text,
        fontSize:      11,
        fontFamily:    "'GeistMono', monospace",
      },
      grid: {
        vertLines:   { color: COLORS.grid },
        horzLines:   { color: COLORS.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine:  { color: COLORS.crosshair, labelBackgroundColor: "#1f2937" },
        horzLine:  { color: COLORS.crosshair, labelBackgroundColor: "#1f2937" },
      },
      rightPriceScale: {
        borderColor: COLORS.border,
        scaleMargins: { top: 0.1, bottom: showVolume ? 0.25 : 0.05 },
      },
      timeScale: {
        borderColor:     COLORS.border,
        barSpacing:      8,
        rightOffset:     8,
        timeVisible:     true,
        secondsVisible:  false,
      },
    });

    // Candlestick series
    const candleSeries = chart.addCandlestickSeries({
      upColor:          COLORS.bullCandle,
      downColor:        COLORS.bearCandle,
      borderUpColor:    COLORS.bullCandle,
      borderDownColor:  COLORS.bearCandle,
      wickUpColor:      COLORS.bullWick,
      wickDownColor:    COLORS.bearWick,
    });

    // Parse bars
    interface Bar { time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number; }
    const bars: Bar[] = rawBars.map((b: any) => ({
      time: Math.floor(new Date(item.timestamp).getTime() / 1000) as UTCTimestamp,
      open:   b.open, high: b.high, low: b.low, close: b.close, volume: b.volume,
    })).sort((a: Bar, b: Bar) => a.time - b.time);

    candleSeries.setData(bars);
    const closes = bars.map((b: Bar) => b.close);

    // EMA overlays
    if (showEMA && bars.length >= 200) {
      const ema50  = calcEMA(closes, 50);
      const ema200 = calcEMA(closes, 200);

      const ema50Series = chart.addLineSeries({
        color: COLORS.ema50, lineWidth: 1.5,
        title: "EMA 50", priceLineVisible: false,
      });
      const ema200Series = chart.addLineSeries({
        color: COLORS.ema200, lineWidth: 1.5,
        title: "EMA 200", priceLineVisible: false,
      });

      ema50Series.setData(
        bars.map((b: Bar, i: number) => ema50[i] != null ? { time: b.time, value: ema50[i]! } : null)
          .filter(Boolean) as any
      );
      ema200Series.setData(
        bars.map((b: Bar, i: number) => ema200[i] != null ? { time: b.time, value: ema200[i]! } : null)
          .filter(Boolean) as any
      );
    }

    // Bollinger Bands
    if (showBB && bars.length >= 20) {
      const bb = calcBB(closes);
      const bbOpts = { lineWidth: 1 as const, priceLineVisible: false, lastValueVisible: false };

      const bbUpSeries  = chart.addLineSeries({ ...bbOpts, color: COLORS.bbUpper, title: "BB Upper", lineStyle: LineStyle.Dashed });
      const bbMidSeries = chart.addLineSeries({ ...bbOpts, color: COLORS.bbMid,   title: "BB Mid",   lineStyle: LineStyle.Dotted });
      const bbLoSeries  = chart.addLineSeries({ ...bbOpts, color: COLORS.bbLower, title: "BB Lower", lineStyle: LineStyle.Dashed });

      const mkSeries = (vals: (number | null)[]) =>
        bars.map((b: Bar, i: number) => vals[i] != null ? { time: b.time, value: vals[i]! } : null)
          .filter(Boolean) as any;

      bbUpSeries.setData(mkSeries(bb.upper));
      bbMidSeries.setData(mkSeries(bb.mid));
      bbLoSeries.setData(mkSeries(bb.lower));
    }

    // Volume histogram
    if (showVolume) {
      const volSeries = chart.addHistogramSeries({
        priceFormat:     { type: "volume" },
        priceScaleId:    "volume",
        color:           COLORS.volBull,
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
      });
      volSeries.setData(
        bars.map((b: Bar) => ({
          time:  b.time,
          value: b.volume,
          color: b.close >= b.open ? COLORS.volBull : COLORS.volBear,
        }))
      );
    }

    chart.timeScale().fitContent();

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.resize(containerRef.current.clientWidth, chart.options().height as number);
    });
    if (containerRef.current) ro.observe(containerRef.current);

    chartRef.current = chart;
    cleanupRef.current = () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, [rawBars, showEMA, showBB, showVolume, height]);

  useEffect(() => { buildChart(); return () => { cleanupRef.current?.(); }; }, [buildChart]);

  return (
    <div className="relative w-full rounded-xl overflow-hidden bg-[#0d1117] border border-border">
      {/* Chart header */}
      <div className="flex items-center gap-4 px-4 py-3 border-b border-border/50">
        <span className="text-xs font-bold font-mono text-foreground">{ticker} · Daily</span>
        <div className="flex items-center gap-3 ml-2">
          {showEMA && <>
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="w-4 h-0.5 rounded" style={{ background: COLORS.ema50 }} />EMA 50
            </span>
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="w-4 h-0.5 rounded" style={{ background: COLORS.ema200 }} />EMA 200
            </span>
          </>}
          {showBB && (
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="w-4 h-0.5 rounded" style={{ background: COLORS.bbUpper }} />BB (20,2)
            </span>
          )}
        </div>
      </div>

      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0d1117]/80 z-10">
          <div className="flex flex-col items-center gap-3">
            <div className="w-6 h-6 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            <span className="text-xs text-muted-foreground">Loading chart…</span>
          </div>
        </div>
      )}

      <div ref={containerRef} style={{ height }} />
    </div>
  );
}
