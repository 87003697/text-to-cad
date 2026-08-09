#!/usr/bin/env node
import {
  buildCanonicalImplicitCad,
  rebuildCanonicalImplicitCad,
} from "../src/lib/implicitCad/canonicalBuild.js";

function usage() {
  return `Usage:
  node scripts/canonical-build.mjs --source <model.implicit.js> --output-dir <relative-directory> [--json]
  node scripts/canonical-build.mjs --recipe <rebuild.json> --output-dir <relative-directory> [--json]

Paths are relative to the current working directory. Exactly one of --source or --recipe is required.
`;
}

function parseArgs(argv) {
  const options = {
    sourcePath: "",
    recipePath: "",
    outputDirectory: "",
    json: false,
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const readValue = () => {
      index += 1;
      if (index >= argv.length) {
        throw new Error(`${arg} requires a value`);
      }
      return argv[index];
    };
    switch (arg) {
      case "--source":
        options.sourcePath = readValue();
        break;
      case "--recipe":
        options.recipePath = readValue();
        break;
      case "--output-dir":
        options.outputDirectory = readValue();
        break;
      case "--json":
        options.json = true;
        break;
      case "--help":
      case "-h":
        options.help = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(usage());
    return;
  }
  if (Boolean(options.sourcePath) === Boolean(options.recipePath)) {
    throw new Error("Exactly one of --source or --recipe is required");
  }
  if (!options.outputDirectory) {
    throw new Error("Missing --output-dir");
  }
  const request = {
    workspaceDirectory: process.cwd(),
    outputDirectory: options.outputDirectory,
  };
  const result = options.recipePath
    ? await rebuildCanonicalImplicitCad({ ...request, recipePath: options.recipePath })
    : await buildCanonicalImplicitCad({ ...request, sourcePath: options.sourcePath });
  if (options.json) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } else {
    process.stdout.write(`Built canonical implicit delivery at ${result.outputDirectory}\n`);
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
