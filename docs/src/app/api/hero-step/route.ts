import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const workspaceRoot = path.resolve(process.cwd(), "..");
const heroStepPath = path.join(
  workspaceRoot,
  "models/fun/planetary_gear_assembly.step"
);
const heroStepGlbPath = path.join(
  workspaceRoot,
  "models/fun/.planetary_gear_assembly.step.glb"
);

export async function GET() {
  try {
    const [sourceStats, glbStats] = await Promise.all([
      stat(heroStepPath),
      stat(heroStepGlbPath),
    ]);

    if (sourceStats.mtimeMs > glbStats.mtimeMs + 1000) {
      return NextResponse.json(
        {
          error: "STEP preview cache is stale",
          detail:
            "Regenerate the adjacent hidden GLB sidecar for models/fun/planetary_gear_assembly.step.",
          source: path.relative(workspaceRoot, heroStepPath),
        },
        {
          headers: {
            "cache-control": "no-store, max-age=0",
            "x-step-glb-mtime": String(Math.round(glbStats.mtimeMs)),
            "x-step-source-mtime": String(Math.round(sourceStats.mtimeMs)),
          },
          status: 409,
        }
      );
    }

    const body = await readFile(heroStepGlbPath);

    return new NextResponse(body, {
      headers: {
        "cache-control": "no-store, max-age=0",
        "content-type": "model/gltf-binary",
        "x-step-glb-mtime": String(Math.round(glbStats.mtimeMs)),
        "x-step-source-mtime": String(Math.round(sourceStats.mtimeMs)),
        "x-step-source": path.relative(workspaceRoot, heroStepPath),
      },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to load STEP preview";

    return NextResponse.json(
      {
        error: "STEP preview cache is missing",
        detail: message,
        source: path.relative(workspaceRoot, heroStepPath),
      },
      { status: 404 }
    );
  }
}
