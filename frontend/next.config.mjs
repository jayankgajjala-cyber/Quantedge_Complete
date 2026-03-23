/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  // output: "standalone" enables efficient Docker/Render deployment
  // Comment this out if deploying to Vercel (Vercel handles it automatically)
  // output: "standalone",

  // Expose the backend API URL to the browser bundle.
  // Set NEXT_PUBLIC_API_URL in your .env.local or hosting platform env vars.
  // Example: NEXT_PUBLIC_API_URL=https://quantedge-backend.onrender.com
  // Default falls back to localhost:8000 for local development.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
};

export default nextConfig;
