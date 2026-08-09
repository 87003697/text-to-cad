import fs from "node:fs/promises";
import vm from "node:vm";

import { IMPLICIT_CANONICAL_PROFILE } from "./canonicalBuild.js";
import { exportImplicitCadModel } from "./exportModel.js";
import { normalizeImplicitCadModel } from "./model.js";

function canonicalVectorMatch(value, expected) {
  return (Array.isArray(value) || ArrayBuffer.isView(value))
    && value.length === expected.length
    && expected.every((coordinate, axis) => value[axis] === coordinate);
}

function authoredCanonicalBoundsMatch(value) {
  const bounds = Array.isArray(value) && value.length === 2
    ? { min: value[0], max: value[1] }
    : value;
  return bounds !== null
    && typeof bounds === "object"
    && canonicalVectorMatch(bounds.min, IMPLICIT_CANONICAL_PROFILE.canonical_bounds[0])
    && canonicalVectorMatch(bounds.max, IMPLICIT_CANONICAL_PROFILE.canonical_bounds[1]);
}

function canonicalBoundsMatch(definition, model) {
  const expected = IMPLICIT_CANONICAL_PROFILE.canonical_bounds;
  return authoredCanonicalBoundsMatch(definition?.bounds)
    && model.boundsSource === "explicit"
    && [0, 1, 2].every((axis) => (
      model.bounds.min[axis] === expected[0][axis]
      && model.bounds.max[axis] === expected[1][axis]
    ));
}

function unwrapCanonicalModule(moduleNamespace) {
  if (moduleNamespace.default !== undefined) {
    return typeof moduleNamespace.default === "function"
      ? moduleNamespace.default()
      : moduleNamespace.default;
  }
  if (moduleNamespace.model !== undefined) {
    return typeof moduleNamespace.model === "function"
      ? moduleNamespace.model()
      : moduleNamespace.model;
  }
  return moduleNamespace;
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf-8");
}

async function loadRestrictedSource(sourceText) {
  const context = vm.createContext({}, {
    codeGeneration: { strings: false, wasm: false },
    name: "implicit-canonical-source",
  });
  vm.runInContext(`
    {
      const forbidden = (name) => function forbiddenAmbientInput() {
        throw new Error(\`deterministic canonical source cannot use \${name}\`);
      };
      Object.defineProperty(Math, "random", {
        configurable: false,
        value: forbidden("Math.random"),
        writable: false
      });
      Object.defineProperty(globalThis, "Date", {
        configurable: false,
        value: class ForbiddenDate {
          constructor() { throw new Error("deterministic canonical source cannot use Date"); }
          static now() { throw new Error("deterministic canonical source cannot use Date.now"); }
        },
        writable: false
      });
      Object.defineProperty(globalThis, "Intl", {
        configurable: false,
        value: new Proxy({}, {
          get() { throw new Error("deterministic canonical source cannot use Intl"); }
        }),
        writable: false
      });
      if ("Temporal" in globalThis) {
        Object.defineProperty(globalThis, "Temporal", {
          configurable: false,
          value: new Proxy({}, {
            get() { throw new Error("deterministic canonical source cannot use Temporal"); }
          }),
          writable: false
        });
      }
      const localeMethods = [
        [Object.prototype, ["toLocaleString"]],
        [Number.prototype, ["toLocaleString"]],
        [BigInt.prototype, ["toLocaleString"]],
        [Array.prototype, ["toLocaleString"]],
        [Object.getPrototypeOf(Uint8Array.prototype), ["toLocaleString"]],
        [String.prototype, [
          "localeCompare",
          "toLocaleLowerCase",
          "toLocaleString",
          "toLocaleUpperCase"
        ]]
      ];
      for (const [prototype, methods] of localeMethods) {
        for (const method of methods) {
          Object.defineProperty(prototype, method, {
            configurable: false,
            value: forbidden(\`locale method \${method}\`),
            writable: false
          });
        }
      }
      Object.freeze(Math);
    }
  `, context);
  const module = new vm.SourceTextModule(sourceText, {
    context,
    identifier: "inline://canonical-source.implicit.js",
    initializeImportMeta(meta) {
      meta.url = "inline://canonical-source.implicit.js";
    },
    importModuleDynamically() {
      throw new Error("canonical implicit source imports are not permitted");
    },
  });
  await module.link(() => {
    throw new Error("canonical implicit source imports are not permitted");
  });
  await module.evaluate({ timeout: 1000 });
  const definition = unwrapCanonicalModule(module.namespace);
  return {
    definition,
    model: normalizeImplicitCadModel({ default: definition }, {
      sourceUrl: "source/canonical-source.implicit.js",
    }),
  };
}

async function main() {
  const outputPath = process.argv[2];
  if (!outputPath) {
    throw new Error("Missing restricted export output path");
  }
  const sourceText = await readStdin();
  const { definition, model } = await loadRestrictedSource(sourceText);
  if (model.units !== "unitless") {
    throw new Error("Canonical implicit source must declare units: \"unitless\"; unit conversion is not permitted");
  }
  if (!canonicalBoundsMatch(definition, model)) {
    throw new Error("Canonical implicit source must declare exact [-0.5, 0.5]^3 authored bounds; automatic bounds fit is not permitted");
  }
  const result = exportImplicitCadModel(model, {
    format: IMPLICIT_CANONICAL_PROFILE.export.format,
    resolution: IMPLICIT_CANONICAL_PROFILE.sampling.resolution,
    maxCells: IMPLICIT_CANONICAL_PROFILE.sampling.max_cells,
    normalEpsilon: IMPLICIT_CANONICAL_PROFILE.sampling.normal_epsilon,
    smoothNormals: IMPLICIT_CANONICAL_PROFILE.export.smooth_normals,
  });
  await fs.writeFile(outputPath, result.body);
  process.stdout.write(JSON.stringify({
    triangleCount: result.mesh.triangleCount,
    vertexCount: result.mesh.vertexCount,
    grid: result.mesh.grid,
  }));
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
