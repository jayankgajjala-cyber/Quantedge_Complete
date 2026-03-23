"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart3, Briefcase, FlaskConical, Newspaper,
  Settings, ChevronLeft, ChevronRight, LogOut,
  TrendingUp, Zap, Trophy,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/lib/store";
import { api } from "@/lib/api";
import { toast } from "sonner";

const NAV = [
  { href: "/portfolio",    label: "Portfolio",      icon: Briefcase,    desc: "Holdings & P&L" },
  { href: "/signals",      label: "Market Signals", icon: Zap,          desc: "Live regime signals" },
  { href: "/research",     label: "News Research",  icon: Newspaper,    desc: "AI sentiment feed" },
  { href: "/paper-trading",label: "Paper Trading",  icon: FlaskConical, desc: "Simulated trades" },
  { href: "/leaderboard",  label: "Leaderboard",    icon: Trophy,       desc: "Strategy rankings" },
  { href: "/settings",     label: "Settings",       icon: Settings,     desc: "Configuration" },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();
  const router   = useRouter();
  const { username, logout } = useAuthStore();

  async function handleLogout() {
    try { await api.post("/auth/logout"); } catch {}
    logout();
    router.replace("/login");
    toast.info("Logged out");
  }

  return (
    <aside className={cn(
      "relative flex flex-col bg-card border-r border-border transition-all duration-300 shrink-0",
      collapsed ? "w-[64px]" : "w-[220px]"
    )}>
      {/* Logo */}
      <div className={cn(
        "flex items-center gap-3 px-4 py-5 border-b border-border",
        collapsed && "justify-center px-0"
      )}>
        <div className="w-8 h-8 rounded-lg bg-primary/20 border border-primary/30 flex items-center justify-center shrink-0">
          <TrendingUp size={14} className="text-primary" />
        </div>
        {!collapsed && (
          <div className="animate-fade-in">
            <div className="font-display font-bold text-sm tracking-tight leading-none">QUANTEDGE</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">Trading Intelligence</div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto">
        {NAV.map(({ href, label, icon: Icon, desc }) => {
          const active = pathname === href || (href !== "/portfolio" && pathname.startsWith(href));
          return (
            <Link key={href} href={href}
              className={cn(
                "group flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all relative",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
                collapsed && "justify-center px-0"
              )}>
              {active && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-primary rounded-r-full" />
              )}
              <Icon size={16} className="shrink-0" />
              {!collapsed && (
                <div className="min-w-0 animate-fade-in">
                  <div className="text-xs font-semibold leading-none">{label}</div>
                  <div className="text-[10px] text-muted-foreground mt-0.5 truncate">{desc}</div>
                </div>
              )}
              {/* Tooltip on collapsed */}
              {collapsed && (
                <div className="absolute left-full ml-3 px-2.5 py-1.5 bg-card border border-border rounded-lg text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-xl">
                  {label}
                </div>
              )}
            </Link>
          );
        })}
      </nav>

      {/* User & logout */}
      <div className={cn(
        "border-t border-border p-3",
        collapsed ? "flex justify-center" : ""
      )}>
        {!collapsed ? (
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/20 flex items-center justify-center shrink-0">
              <span className="text-primary text-[10px] font-bold uppercase">{username?.[0] || "J"}</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold truncate">{username || "User"}</div>
              <div className="text-[10px] text-muted-foreground">Administrator</div>
            </div>
            <button onClick={handleLogout}
              className="text-muted-foreground hover:text-bear transition-colors p-1 rounded">
              <LogOut size={13} />
            </button>
          </div>
        ) : (
          <button onClick={handleLogout}
            className="text-muted-foreground hover:text-bear transition-colors p-2 rounded-xl">
            <LogOut size={14} />
          </button>
        )}
      </div>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -right-3 top-[72px] w-6 h-6 rounded-full bg-card border border-border flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors z-10 shadow-md">
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>
    </aside>
  );
}
