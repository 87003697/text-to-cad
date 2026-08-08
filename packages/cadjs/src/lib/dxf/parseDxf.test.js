import assert from "node:assert/strict";
import test from "node:test";

import { parseDxf } from "./parseDxf.js";

function dxfText(lines) {
  return `${lines.join("\n")}\n`;
}

test("parseDxf normalizes closed lwpolyline, circles, and bends", () => {
  const payload = parseDxf(dxfText([
    "0", "SECTION",
    "2", "HEADER",
    "0", "ENDSEC",
    "0", "SECTION",
    "2", "ENTITIES",
    "0", "LWPOLYLINE",
    "8", "CUT",
    "90", "4",
    "70", "1",
    "10", "0",
    "20", "0",
    "10", "10",
    "20", "0",
    "10", "10",
    "20", "5",
    "10", "0",
    "20", "5",
    "0", "CIRCLE",
    "8", "CUT",
    "10", "5",
    "20", "2.5",
    "40", "1",
    "0", "LINE",
    "8", "BEND",
    "10", "3",
    "20", "0",
    "11", "3",
    "21", "5",
    "0", "ENDSEC",
    "0", "EOF"
  ]), { fileRef: "test/panel.dxf" });

  assert.equal(payload.fileRef, "test/panel.dxf");
  assert.equal(payload.defaultThicknessMm, 0);
  assert.equal(payload.bounds.width, 10);
  assert.equal(payload.bounds.height, 5);
  assert.equal(payload.counts.paths, 5);
  assert.equal(payload.counts.circles, 1);
  assert.equal(payload.geometry.lines.length, 5);
  assert.equal(payload.layers.length, 2);
  assert.deepEqual(payload.layers.map((layer) => layer.name), ["BEND", "CUT"]);
});

test("parseDxf converts lwpolyline bulges into arcs", () => {
  const quarterBulge = Math.tan(Math.PI / 8);
  const payload = parseDxf(dxfText([
    "0", "SECTION",
    "2", "ENTITIES",
    "0", "LWPOLYLINE",
    "8", "CUT",
    "90", "4",
    "70", "1",
    "10", "1",
    "20", "0",
    "42", String(quarterBulge),
    "10", "0",
    "20", "1",
    "42", String(quarterBulge),
    "10", "-1",
    "20", "0",
    "42", String(quarterBulge),
    "10", "0",
    "20", "-1",
    "42", String(quarterBulge),
    "0", "ENDSEC",
    "0", "EOF"
  ]), { fileRef: "test/bulged-hole.dxf" });

  assert.equal(payload.counts.paths, 4);
  assert.equal(payload.counts.circles, 0);
  assert.equal(payload.geometry.lines.length, 0);
  assert.equal(payload.geometry.arcs.length, 4);
  assert.equal(payload.bounds.width, 2);
  assert.equal(payload.bounds.height, 2);
  assert.deepEqual(payload.geometry.arcs.map((arc) => arc.radius), [1, 1, 1, 1]);
  assert.deepEqual(payload.geometry.arcs.map((arc) => arc.sweepAngleDeg), [90, 90, 90, 90]);
  assert.match(payload.paths[0].d, /A 1 1 0 0 0 /);
});

test("an ellipse becomes a closed sampled contour", () => {
  const dxf = [
    "0", "SECTION", "2", "ENTITIES",
    "0", "ELLIPSE", "8", "CUT",
    "10", "0", "20", "0",       // centre
    "11", "10", "21", "0",      // major axis vector
    "40", "0.5",                // minor/major ratio
    "41", "0", "42", `${Math.PI * 2}`,
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  const data = parseDxf(dxf, { fileRef: "e.dxf" });

  assert.ok(data.geometry.lines.length > 8, "sampled into segments");
  const first = data.geometry.lines[0].start;
  const last = data.geometry.lines[data.geometry.lines.length - 1].end;
  assert.ok(Math.hypot(first[0] - last[0], first[1] - last[1]) < 1e-9, "closes on itself");
});

test("a legacy POLYLINE reads its VERTEX entities", () => {
  // Unlike LWPOLYLINE the vertices are separate entities terminated by SEQEND, so the walker
  // has to consume them rather than the entity parser reading them inline.
  const dxf = [
    "0", "SECTION", "2", "ENTITIES",
    "0", "POLYLINE", "8", "CUT", "70", "1",
    "0", "VERTEX", "10", "0", "20", "0",
    "0", "VERTEX", "10", "10", "20", "0",
    "0", "VERTEX", "10", "10", "20", "10",
    "0", "SEQEND",
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  const data = parseDxf(dxf, { fileRef: "p.dxf" });

  // Three vertices, closed flag set -> three segments.
  assert.equal(data.geometry.lines.length, 3);
});

test("an INSERT places its block's geometry", () => {
  const dxf = [
    "0", "SECTION", "2", "BLOCKS",
    "0", "BLOCK", "2", "SQ",
    "0", "LINE", "8", "CUT", "10", "0", "20", "0", "11", "1", "21", "0",
    "0", "ENDBLK",
    "0", "ENDSEC",
    "0", "SECTION", "2", "ENTITIES",
    "0", "INSERT", "2", "SQ", "10", "5", "20", "7",
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  const data = parseDxf(dxf, { fileRef: "i.dxf" });

  assert.equal(data.geometry.lines.length, 1);
  assert.deepEqual(data.geometry.lines[0].start, [5, 7], "translated by the insertion point");
});

test("a missing block is empty rather than fatal", () => {
  // Drawings reference blocks they no longer define; one dangling name must not cost the
  // whole profile.
  const dxf = [
    "0", "SECTION", "2", "ENTITIES",
    "0", "LINE", "8", "CUT", "10", "0", "20", "0", "11", "1", "21", "1",
    "0", "INSERT", "2", "GONE", "10", "0", "20", "0",
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  assert.equal(parseDxf(dxf, { fileRef: "m.dxf" }).geometry.lines.length, 1);
});

test("annotation entities are skipped, not rejected", () => {
  // A drawing is not unrenderable because it is dimensioned. Rejecting it over a DIMENSION
  // is how a perfectly cuttable profile ends up showing an error card.
  const dxf = [
    "0", "SECTION", "2", "ENTITIES",
    "0", "LINE", "8", "CUT", "10", "0", "20", "0", "11", "1", "21", "1",
    "0", "DIMENSION", "8", "DIMS", "10", "0", "20", "0",
    "0", "MTEXT", "8", "NOTES", "1", "hello",
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  assert.equal(parseDxf(dxf, { fileRef: "a.dxf" }).geometry.lines.length, 1);
});

test("a genuinely unknown entity is still reported", () => {
  const dxf = [
    "0", "SECTION", "2", "ENTITIES",
    "0", "NOTAREALENTITY", "8", "CUT",
    "0", "ENDSEC", "0", "EOF", "",
  ].join("\n");

  assert.throws(() => parseDxf(dxf, { fileRef: "u.dxf" }), /Unsupported DXF entity NOTAREALENTITY/u);
});
