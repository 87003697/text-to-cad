import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import {
  isCatalogRelevantPath,
} from "cadjs/lib/cadDirectoryScanner.mjs";
import {
  normalizeViewerDefaultFile,
  normalizeViewerGithubUrl,
} from "cadjs/lib/viewerConfig.mjs";
import {
  DEFAULT_VIEWER_PORT,
  buildViewerServerInfo,
  normalizeViewerPort,
} from "cadjs/lib/viewerServerInfo.mjs";
import {
  removeViewerServerRegistryEntry,
  writeViewerServerRegistry,
} from "cadjs/lib/viewerServerRegistry.mjs";
import {
  pathIsInside,
  resolveWorkspaceRoot as resolveViewerWorkspaceRoot,
} from "cadjs/lib/pathUtils.mjs";
import { createLocalAssetBackend } from "./src/server/localAssetBackend.mjs";
import {
  createCadViewerApiMiddleware,
  createLocalAssetMiddleware,
} from "./src/server/httpHandlers.mjs";
import {
  localRootDirFromEnv,
  normalizeViewerAssetBackend,
  rootDirForAssetBackend,
} from "./src/server/viewerEnv.mjs";

const viewerPort = normalizeViewerPort(process.env.VIEWER_PORT, DEFAULT_VIEWER_PORT);
const viewerAppRoot = path.dirname(fileURLToPath(import.meta.url));
const viewerClientRoot = path.join(viewerAppRoot, "src", "client");
const cadJsPackageRoot = resolveCadJsPackageRoot();
const viewerNodeModulesRoot = path.join(viewerAppRoot, "node_modules");
const defaultWorkspaceRoot = path.resolve(viewerAppRoot, "..");
const workspaceRoot = resolveWorkspaceRoot();
const repoRoot = workspaceRoot;
const buildViewerAssetBackend = normalizeViewerAssetBackend(process.env.VIEWER_ASSET_BACKEND);
const buildViewerRootDir = rootDirForAssetBackend(buildViewerAssetBackend, process.env);
const buildViewerDefaultFile = normalizeViewerDefaultFile(process.env.VIEWER_DEFAULT_FILE ?? "");
const buildViewerGithubUrl = normalizeViewerGithubUrl(process.env.VIEWER_GITHUB_URL ?? "");
const viewerAllowedHosts = normalizeViewerAllowedHosts(process.env.VIEWER_ALLOWED_HOSTS ?? "");
const localAssetBackend = createLocalAssetBackend({
  workspaceRoot,
  rootDir: localRootDirFromEnv(process.env),
  defaultFile: buildViewerDefaultFile,
  githubUrl: buildViewerGithubUrl,
});

function normalizeViewerAllowedHosts(value) {
  return String(value || "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
}

function findRootPackageSrc(packageDirName) {
  let current = viewerAppRoot;
  for (;;) {
    const candidate = path.join(current, "packages", packageDirName, "src");
    if (
      fs.existsSync(candidate) &&
      fs.existsSync(path.join(current, "packages", packageDirName, "package.json"))
    ) {
      return candidate;
    }
    const next = path.dirname(current);
    if (next === current) {
      return "";
    }
    current = next;
  }
}

function resolveCadJsPackageRoot() {
  const installedPackageSrc = path.join(viewerAppRoot, "node_modules", "cadjs", "src");
  if (fs.existsSync(installedPackageSrc)) {
    return installedPackageSrc;
  }
  const rootPackageSrc = findRootPackageSrc("cadjs");
  return rootPackageSrc || path.resolve(viewerAppRoot, "../packages/cadjs/src");
}

function resolveWorkspaceRoot() {
  return resolveViewerWorkspaceRoot({
    env: process.env,
    cwd: process.cwd(),
    appRoot: viewerAppRoot,
    defaultWorkspaceRoot,
  });
}

function viteServerPort(server) {
  const address = server?.httpServer?.address?.();
  return address && typeof address === "object" && Number.isInteger(address.port)
    ? address.port
    : viewerPort;
}

function cadCatalogPlugin({ enableStepArtifactBackend = false } = {}) {
  const activeDirectories = new Map();
  const refreshTimers = new Map();

  function activateDirectory(server, rootDir) {
    const resolved = localAssetBackend.resolveRoot(rootDir);
    const wasActive = activeDirectories.has(resolved.rootPath);
    activeDirectories.set(resolved.rootPath, resolved.dir);
    if (!wasActive) {
      server.watcher.add(resolved.rootPath);
    }
    return resolved;
  }

  function scheduleCatalogRefresh(server, rootPath, dir) {
    if (refreshTimers.has(rootPath)) {
      clearTimeout(refreshTimers.get(rootPath));
    }
    refreshTimers.set(rootPath, setTimeout(() => {
      refreshTimers.delete(rootPath);
      try {
        localAssetBackend.refreshCatalog({ rootDir: dir });
      } catch (error) {
        console.warn("Failed to refresh CAD catalog", error);
      }
      server.ws.send({
        type: "custom",
        event: "cad-catalog:changed",
        data: { dir },
      });
    }, 150));
  }

  function notifyChangedPath(server, changedPath) {
    const resolvedChangedPath = path.resolve(changedPath);
    if (localAssetBackend.isGenerationStatusPath?.(resolvedChangedPath)) {
      server.ws.send({
        type: "custom",
        event: "cad-generation-status:changed",
      });
      return;
    }
    if (!isCatalogRelevantPath(resolvedChangedPath)) {
      return;
    }
    for (const [rootPath, dir] of activeDirectories.entries()) {
      if (resolvedChangedPath === rootPath || pathIsInside(resolvedChangedPath, rootPath)) {
        scheduleCatalogRefresh(server, rootPath, dir);
      }
    }
  }

  return {
    name: "cad-catalog",
    configureServer(server) {
      const servedViewerRoot = activateDirectory(server, buildViewerRootDir);
      server.watcher.add(localAssetBackend.generationStatusDir());
      localAssetBackend.refreshCatalog({ rootDir: buildViewerRootDir });
      let activeServerInfo = null;
      const currentServerInfo = () => {
        activeServerInfo = buildViewerServerInfo({
          workspaceRoot: repoRoot,
          rootDir: buildViewerRootDir,
          port: viteServerPort(server),
          pid: process.pid,
        });
        return activeServerInfo;
      };
      const registerServer = () => {
        writeViewerServerRegistry(currentServerInfo());
      };
      if (server.httpServer?.listening) {
        registerServer();
      } else {
        server.httpServer?.once("listening", registerServer);
      }
      server.httpServer?.once("close", () => {
        removeViewerServerRegistryEntry(activeServerInfo || currentServerInfo());
      });
      server.middlewares.use(createCadViewerApiMiddleware({
        backend: localAssetBackend,
        rootDir: buildViewerRootDir,
        enableStepArtifactBackend,
        serverInfo: currentServerInfo,
        onCatalogChanged: (resolvedRoot) => {
          scheduleCatalogRefresh(server, resolvedRoot.rootPath, resolvedRoot.dir);
        },
      }));
      server.middlewares.use(createLocalAssetMiddleware({
        backend: localAssetBackend,
        rootDir: servedViewerRoot.dir,
      }));
      for (const eventName of ["add", "change", "unlink"]) {
        server.watcher.on(eventName, (changedPath) => notifyChangedPath(server, changedPath));
      }
    },
  };
}

export default defineConfig(({ command }) => ({
  root: viewerAppRoot,
  envPrefix: "VIEWER_",
  plugins: [react(), cadCatalogPlugin({ enableStepArtifactBackend: command === "serve" })],
  resolve: {
    alias: {
      "@": viewerClientRoot,
      "cadjs": cadJsPackageRoot,
      "clsx": path.join(viewerNodeModulesRoot, "clsx"),
      "gifenc": path.join(viewerNodeModulesRoot, "gifenc", "dist", "gifenc.esm.js"),
      "tailwind-merge": path.join(viewerNodeModulesRoot, "tailwind-merge"),
      "three": path.join(viewerNodeModulesRoot, "three"),
      "three/examples": path.join(viewerNodeModulesRoot, "three", "examples"),
    },
  },
  esbuild: {
    loader: "jsx",
    include: /.*\.[jt]sx?$/,
    exclude: [],
  },
  optimizeDeps: {
    esbuildOptions: {
      loader: {
        ".js": "jsx",
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("/three/")) {
            return "vendor-three";
          }
          if (id.includes("/react/") || id.includes("/react-dom/")) {
            return "vendor-react";
          }
          if (id.includes("/radix-ui/") || id.includes("/@radix-ui/")) {
            return "vendor-ui";
          }
          if (id.includes("/lucide-react/")) {
            return "vendor-icons";
          }
          return undefined;
        },
      },
    },
  },
  worker: {
    format: "es",
  },
  server: {
    host: "127.0.0.1",
    port: viewerPort,
    strictPort: true,
    allowedHosts: viewerAllowedHosts,
  },
  preview: {
    host: "127.0.0.1",
    port: viewerPort,
    strictPort: true,
    allowedHosts: viewerAllowedHosts,
  },
}));
