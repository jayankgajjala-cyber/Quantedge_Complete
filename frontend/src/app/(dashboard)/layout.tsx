"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/store";
import Sidebar from "@/components/layout/Sidebar";
import IntelligenceMarquee from "@/components/layout/IntelligenceMarquee";
import Header from "@/components/layout/Header";
import { SWRConfig } from "swr";
import { fetcher } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const token  = useAuthStore((s) => s.token);
  const router = useRouter();

  useEffect(() => {
    if (!token) router.replace("/login");
  }, [token, router]);

  if (!token) return null;

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
