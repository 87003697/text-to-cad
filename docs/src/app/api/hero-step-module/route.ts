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
const heroStepModulePath = path.join(
  workspaceRoot,
  "models/fun/.planetary_gear_assembly.step.js"
);

export async function GET() {
  try {
    const [sourceStats, moduleStats, body] = await Promise.all([
      stat(heroStepPath),
      stat(heroStepModulePath),
      readFile(heroStepModulePath, "utf8"),
    ]);

    return new NextResponse(body, {
      headers: {
        "cache-control": "no-store, max-age=0",
        "content-type": "application/javascript; charset=utf-8",
        "x-step-module-mtime": String(Math.round(moduleStats.mtimeMs)),
        "x-step-source-mtime": String(Math.round(sourceStats.mtimeMs)),
        "x-step-source": path.relative(workspaceRoot, heroStepPath),
      },
    });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Unable to load STEP preview module";

    return NextResponse.json(
      {
        error: "STEP preview module is missing",
        detail: message,
        source: path.relative(workspaceRoot, heroStepModulePath),
      },
      { status: 404 }
    );
  }
}
