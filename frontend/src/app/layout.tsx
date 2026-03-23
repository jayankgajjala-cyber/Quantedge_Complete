import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Toaster } from "sonner";
import "./globals.css";

const inter = Inter({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Quantedge Trading Dashboard",
  description: "Institutional AI Trading Dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetbrainsMono.variable} antialiased`}>
        {children}
        <Toaster
          richColors
          position="top-right"
          duration={4000}
          toastOptions={{
            style: {
              background: "hsl(222 18% 11%)",
              border: "1px solid hsl(222 12% 18%)",
              color: "hsl(210 20% 94%)",
              fontSize: "13px",
            },
          }}
        />
      </body>
    </html>
  );
}
