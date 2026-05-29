import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_VIEWER_GITHUB_URL,
  normalizeViewerDefaultFile,
  normalizeViewerGithubUrl,
  viewerGithubReleaseUrl,
  viewerGithubRepositoryUrl
} from "./viewerConfig.mjs";

test("normalizeViewerDefaultFile keeps scan-relative file paths", () => {
  assert.equal(normalizeViewerDefaultFile("/STEP/sample_part.step/"), "STEP/sample_part.step");
  assert.equal(normalizeViewerDefaultFile("STEP\\sample_part.step"), "STEP/sample_part.step");
});

test("normalizeViewerGithubUrl defaults to the CAD Viewer repository link", () => {
  assert.equal(normalizeViewerGithubUrl(""), DEFAULT_VIEWER_GITHUB_URL);
});

test("normalizeViewerGithubUrl accepts configured GitHub URLs", () => {
  assert.equal(
    normalizeViewerGithubUrl("github.com/example/repo"),
    "https://github.com/example/repo"
  );
  assert.equal(
    normalizeViewerGithubUrl("https://github.com/example/repo/tree/main"),
    "https://github.com/example/repo/tree/main"
  );
});

test("normalizeViewerGithubUrl falls back to a configured default", () => {
  assert.equal(
    normalizeViewerGithubUrl("", "github.com/example/default"),
    "https://github.com/example/default"
  );
});

test("viewerGithubRepositoryUrl trims GitHub branch paths to the repository", () => {
  assert.equal(
    viewerGithubRepositoryUrl("https://github.com/example/repo/tree/main"),
    "https://github.com/example/repo"
  );
});

test("viewerGithubReleaseUrl links to the requested release tag", () => {
  assert.equal(
    viewerGithubReleaseUrl("0.1.10", "github.com/example/repo/tree/main"),
    "https://github.com/example/repo/releases/tag/0.1.10"
  );
});
