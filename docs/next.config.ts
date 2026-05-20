import type { NextConfig } from "next";
import path from "node:path";

const repoRoot = path.resolve(process.cwd(), "..");
const renderViewerRoot = path.join(repoRoot, "skills/render/scripts/viewer");
const docsThreeRoot = "./node_modules/three";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    externalDir: true,
  },
  images: {
    remotePatterns: [
      {
        hostname: "www.skills.sh",
        protocol: "https",
      },
    ],
  },
  turbopack: {
    root: repoRoot,
    resolveAlias: {
      "@render-viewer": renderViewerRoot,
      three: docsThreeRoot,
    },
  },
};

export default nextConfig;
