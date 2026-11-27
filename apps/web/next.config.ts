import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@kinetiq/contracts", "@kinetiq/remotion-studio"],
};

export default nextConfig;
