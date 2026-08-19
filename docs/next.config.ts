import type { NextConfig } from "next";
import path from "node:path";

const repoRoot = path.resolve(process.cwd(), "..");
const cadJsPackageRoot = path.join(repoRoot, "packages/cadjs/src");
const docsThreeRoot = "./node_modules/three";
const docsThreeExamplesRoot = "./node_modules/three/examples";
const docsMeshoptimizer = "./node_modules/meshoptimizer";
const threeExample = (subpath: string) =>
  `./node_modules/three/examples/jsm/${subpath}`;

// Every bare specifier cadjs imports needs an entry here, and each deep subpath needs its
// OWN entry -- the "three/examples" alias above does not cover paths beneath it, which is
// why GLTFLoader was already listed individually.
//
// The reason aliases are load-bearing rather than a convenience: these imports live in
// packages/cadjs/src, outside docs/, so Node resolution walks up from THERE --
// packages/cadjs/node_modules, packages/node_modules, <repo>/node_modules -- and never
// reaches docs/node_modules. In a dev checkout packages/cadjs/node_modules exists and the
// build works by accident; on Vercel only docs/ is installed and it fails. Reproduce that
// locally by moving packages/cadjs/node_modules aside before building.
const cadJsBareImports = {
  meshoptimizer: docsMeshoptimizer,
  "three/examples/jsm/loaders/GLTFLoader.js": threeExample(
    "loaders/GLTFLoader.js",
  ),
  "three/examples/jsm/loaders/3MFLoader.js": threeExample(
    "loaders/3MFLoader.js",
  ),
  "three/examples/jsm/loaders/STLLoader.js": threeExample(
    "loaders/STLLoader.js",
  ),
  "three/examples/jsm/libs/fflate.module.js": threeExample(
    "libs/fflate.module.js",
  ),
  "three/examples/jsm/utils/BufferGeometryUtils.js": threeExample(
    "utils/BufferGeometryUtils.js",
  ),
};

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
      "cadjs": cadJsPackageRoot,
      three: docsThreeRoot,
      "three/examples": docsThreeExamplesRoot,
      ...cadJsBareImports,
    },
  },
};

export default nextConfig;
