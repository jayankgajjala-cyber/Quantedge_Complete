"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/store";
import Sidebar from "@/components/layout/Sidebar";
import IntelligenceMarquee from "@/components/layout/IntelligenceMarquee";
import Header from "@/components/layout/Header";
import { SWRConfig } from "swr";
import { fetcher } from "@/lib/api";

function LoadingSkeleton() {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <div className="w-[220px] border-r border-border bg-card shrink-0" />
      <div className="flex-1 flex flex-col">
        <div className="h-14 border-b border-border bg-card/80" />
        <div className="h-7 border-b border-border bg-card/60" />
        <div className="flex-1 p-5 space-y-4">
          <div className="h-6 w-48 rounded-lg bg-muted/60 animate-pulse" />
          <div className="grid grid-cols-4 gap-3">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-20 rounded-2xl bg-muted/40 animate-pulse" />
            ))}
          </div>
          <div className="h-64 rounded-2xl bg-muted/40 animate-pulse" />
        </div>
      </div>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const token       = useAuthStore((s) => s.token);
  const hasHydrated = useAuthStore((s) => s._hasHydrated);
  const router      = useRouter();

  useEffect(() => {
    if (hasHydrated && !token) {
      router.replace("/login");
    }
  }, [token, hasHydrated, router]);

  if (!hasHydrated) return <LoadingSkeleton />;
  if (!token) return <LoadingSkeleton />;

  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: true }}>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <Header />
          <IntelligenceMarquee />
          <main className="flex-1 overflow-y-auto p-5">
            {children}
          </main>
        </div>
      </div>
    </SWRConfig>
  );
}
