"use client";
import { useState } from "react";
import { FlaskConical, Plus, X, TrendingUp, TrendingDown, DollarSign, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { usePaperTrades } from "@/hooks/useData";
import { cn, fmt, fmtPct, fmtCurrency, fmtDate } from "@/lib/utils";
import { Card, CardHeader, CardContent, StatCard, Skeleton, Empty, Badge, Tabs } from "@/components/ui";
import type { PaperTrade } from "@/types";

export default function PaperTradingPage() {
  const [statusTab, setStatusTab] = useState("OPEN");
  const [showForm, setShowForm]   = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [closing, setClosing]     = useState<number | null>(null);
  const [exitPrice, setExitPrice] = useState("");

  const { data: trades, isLoading, mutate } = usePaperTrades(statusTab);

  const [form, setForm] = useState({
    symbol: "", direction: "BUY", quantity: "", entry_price: "",
    stop_loss: "", target: "", strategy_name: "",
  });

  const openTrades  = trades?.filter((t) => t.status === "OPEN") ?? [];
  const closedTrades= trades?.filter((t) => t.status === "CLOSED") ?? [];
  const totalPnl    = closedTrades.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const winners     = closedTrades.filter((t) => (t.pnl ?? 0) > 0).length;

  async function handleOpen(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post("/paper-trades/", {
        symbol:        form.symbol.toUpperCase(),
        direction:     form.direction,
        quantity:      parseFloat(form.quantity),
        entry_price:   parseFloat(form.entry_price),
        stop_loss:     form.stop_loss    ? parseFloat(form.stop_loss)    : null,
        target:        form.target       ? parseFloat(form.target)       : null,
        strategy_name: form.strategy_name || null,
      });
      toast.success(`Paper trade opened: ${form.direction} ${form.symbol.toUpperCase()}`);
      mutate();
      setShowForm(false);
      setForm({ symbol: "", direction: "BUY", quantity: "", entry_price: "", stop_loss: "", target: "", strategy_name: "" });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to open trade");
    } finally { setSubmitting(false); }
  }

  async function handleClose(id: number) {
    if (!exitPrice) { toast.error("Enter exit price"); return; }
    setClosing(id);
    try {
      await api.post(`/paper-trades/${id}/close`, { exit_price: parseFloat(exitPrice) });
      toast.success("Trade closed");
      mutate();
      setClosing(null);
      setExitPrice("");
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to close trade");
    } finally { setClosing(null); }
  }

  const inputClass = "w-full bg-muted/50 border border-border rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/30 transition-all";

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Paper Trading</h1>
          <p className="text-muted-foreground text-xs mt-0.5">Simulated trades with real-time P&L tracking</p>
        </div>
        <button onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-primary/10 border border-primary/30 text-primary text-xs font-semibold hover:bg-primary/20 transition-all">
          <Plus size={13} />{showForm ? "Cancel" : "New Trade"}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Open Trades"    value={String(openTrades.length)}  icon={<FlaskConical size={15} />} trend="neutral" />
        <StatCard label="Closed Trades"  value={String(closedTrades.length)} icon={<DollarSign size={15} />} trend="neutral" />
        <StatCard label="Realised P&L"   value={fmtCurrency(totalPnl)}      icon={<TrendingUp size={15} />}
          trend={totalPnl >= 0 ? "up" : "down"} glow />
        <StatCard label="Win Rate"
          value={closedTrades.length > 0 ? `${((winners / closedTrades.length) * 100).toFixed(0)}%` : "—"}
          icon={<TrendingUp size={15} />} trend="neutral" />
      </div>

      {/* New trade form */}
      {showForm && (
        <Card className="animate-slide-in">
          <CardHeader>
            <span className="text-sm font-semibold flex items-center gap-2">
              <Plus size={13} className="text-primary" />Open Paper Trade
            </span>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleOpen} className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { key: "symbol",        label: "Symbol",        placeholder: "RELIANCE", type: "text" },
                { key: "quantity",      label: "Quantity",      placeholder: "10",       type: "number" },
                { key: "entry_price",   label: "Entry Price",   placeholder: "2500.00",  type: "number" },
                { key: "stop_loss",     label: "Stop Loss",     placeholder: "2450.00",  type: "number" },
                { key: "target",        label: "Target",        placeholder: "2600.00",  type: "number" },
                { key: "strategy_name", label: "Strategy",      placeholder: "EMA Cross",type: "text" },
              ].map(({ key, label, placeholder, type }) => (
                <div key={key}>
                  <label className="text-[10px] uppercase tracking-widest text-muted-foreground block mb-1.5">{label}</label>
                  <input
                    type={type}
                    step="any"
                    placeholder={placeholder}
                    value={(form as any)[key]}
                    onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                    className={inputClass}
                    required={["symbol","quantity","entry_price"].includes(key)}
                  />
                </div>
              ))}

              <div>
                <label className="text-[10px] uppercase tracking-widest text-muted-foreground block mb-1.5">Direction</label>
                <select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}
                  className={inputClass}>
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </div>

              <div className="col-span-2 md:col-span-4 flex justify-end gap-2 pt-1">
                <button type="button" onClick={() => setShowForm(false)}
                  className="px-4 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground transition-all">
                  Cancel
                </button>
                <button type="submit" disabled={submitting}
                  className="px-6 py-2 rounded-xl bg-primary/90 hover:bg-primary text-black font-bold text-xs flex items-center gap-2 transition-all disabled:opacity-60">
                  {submitting ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                  Open Trade
                </button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Tabs */}
      <Tabs
        tabs={[
          { key: "OPEN",   label: "Open",   icon: <FlaskConical size={10} /> },
          { key: "CLOSED", label: "Closed", icon: <DollarSign size={10} /> },
        ]}
        active={statusTab}
        onChange={setStatusTab}
      />

      {/* Trades table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-5 space-y-3">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
          ) : !trades?.length ? (
            <Empty icon={<FlaskConical size={28} />} title={`No ${statusTab.toLowerCase()} trades`} description="Open a new paper trade to start tracking" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    {["Symbol","Dir","Qty","Entry","Exit","SL","Target","P&L",
                      statusTab === "OPEN" ? "Action" : "Closed At"
                    ].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t: PaperTrade) => {
                    const pnl = t.pnl ?? 0;
                    return (
                      <tr key={t.id} className="border-b border-border/40 hover:bg-muted/30 transition-colors">
                        <td className="px-4 py-3 font-bold">{t.symbol}</td>
                        <td className="px-4 py-3">
                          <span className={cn("text-[10px] font-bold px-2 py-0.5 rounded-full border",
                            t.direction === "BUY" ? "text-bull bg-bull/10 border-bull/20" : "text-bear bg-bear/10 border-bear/20"
                          )}>{t.direction}</span>
                        </td>
                        <td className="px-4 py-3 font-mono">{fmt(t.quantity, 0)}</td>
                        <td className="px-4 py-3 font-mono">₹{fmt(t.entry_price)}</td>
                        <td className="px-4 py-3 font-mono">{t.exit_price ? `₹${fmt(t.exit_price)}` : "—"}</td>
                        <td className="px-4 py-3 font-mono text-bear">{t.stop_loss ? `₹${fmt(t.stop_loss)}` : "—"}</td>
                        <td className="px-4 py-3 font-mono text-bull">{t.target ? `₹${fmt(t.target)}` : "—"}</td>
                        <td className={cn("px-4 py-3 font-mono font-semibold", t.status === "CLOSED" ? (pnl >= 0 ? "text-bull" : "text-bear") : "text-muted-foreground")}>
                          {t.status === "CLOSED" ? `${pnl >= 0 ? "+" : ""}₹${fmt(Math.abs(pnl))}` : "Open"}
                        </td>
                        <td className="px-4 py-3">
                          {t.status === "OPEN" ? (
                            <div className="flex items-center gap-2">
                              <input
                                type="number" step="any" placeholder="Exit ₹"
                                value={closing === t.id ? exitPrice : ""}
                                onChange={(e) => setExitPrice(e.target.value)}
                                onFocus={() => setClosing(t.id)}
                                className="w-20 bg-muted/50 border border-border rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
                              />
                              <button
                                onClick={() => handleClose(t.id)}
                                disabled={closing === t.id && !exitPrice}
                                className="px-2.5 py-1 rounded-lg bg-bear/10 border border-bear/20 text-bear text-[10px] font-semibold hover:bg-bear/20 transition-all disabled:opacity-40">
                                {closing === t.id ? <Loader2 size={10} className="animate-spin" /> : "Close"}
                              </button>
                            </div>
                          ) : (
                            <span className="text-[10px] text-muted-foreground">
                              {t.exit_time ? fmtDate(t.exit_time) : "—"}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
