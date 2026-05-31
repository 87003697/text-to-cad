import assert from "node:assert/strict";
import test from "node:test";

import { RENDER_FORMAT } from "cadjs/lib/fileFormats.js";

import { stepArtifactCanGenerate } from "./stepArtifactStatus.js";

test("stepArtifactCanGenerate allows buildable STEP artifact warnings", () => {
  const entry = {
    file: "parts/bracket.step",
    artifact: {
      ok: false,
      error: "missing_step_hash",
      sourceKind: "python"
    }
  };

  assert.equal(stepArtifactCanGenerate(entry, RENDER_FORMAT.STEP), true);
});

test("stepArtifactCanGenerate respects backend generation availability", () => {
  const entry = {
    file: "parts/bracket.step",
    artifact: {
      ok: false,
      error: "missing_glb"
    }
  };

  assert.equal(
    stepArtifactCanGenerate(entry, RENDER_FORMAT.STEP, { generationAvailable: false }),
    false
  );
});
