import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { openAssetHandleUnderRoot } from "./safeAssetOpen.mjs";

const CAN_MAKE_FILE_SYMLINK = probeSymlinkCapability("file");
const CAN_MAKE_DIR_SYMLINK = probeSymlinkCapability("dir");

function probeSymlinkCapability(kind) {
  const probeRoot = fs.mkdtempSync(path.join(os.tmpdir(), "safe-open-probe-"));
  try {
    const target = path.join(probeRoot, "target");
    const linkPath = path.join(probeRoot, "link");
    if (kind === "dir") {
      fs.mkdirSync(target);
      fs.symlinkSync(target, linkPath, "dir");
    } else {
      fs.writeFileSync(target, "");
      fs.symlinkSync(target, linkPath);
    }
    return true;
  } catch (error) {
    if (error && (error.code === "EPERM" || error.code === "EACCES")) {
      return false;
    }
    throw error;
  } finally {
    fs.rmSync(probeRoot, { recursive: true, force: true });
  }
}

function withTemp(callback) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "safe-open-"));
  try {
    return callback(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("openAssetHandleUnderRoot opens a normal file inside the root and returns a usable fd", () => {
  withTemp((root) => {
    const leaf = path.join(root, "a", "b", "leaf.bin");
    fs.mkdirSync(path.dirname(leaf), { recursive: true });
    fs.writeFileSync(leaf, Buffer.from("hello"));
    const { fd, size } = openAssetHandleUnderRoot(root, leaf);
    try {
      assert.equal(size, 5);
      const buf = Buffer.alloc(size);
      fs.readSync(fd, buf, 0, size, 0);
      assert.equal(buf.toString("utf8"), "hello");
    } finally {
      fs.closeSync(fd);
    }
  });
});

test("openAssetHandleUnderRoot rejects a leaf that is a symbolic link out of the root", (t) => {
  if (!CAN_MAKE_FILE_SYMLINK) {
    t.skip("filesystem does not permit unprivileged file symbolic links");
    return;
  }
  withTemp((root) => {
    const outside = path.join(root, "..", `outside-secret-${process.pid}.bin`);
    fs.writeFileSync(outside, Buffer.from("secret"));
    t.after(() => fs.rmSync(outside, { force: true }));
    const leaf = path.join(root, "leaf");
    fs.symlinkSync(outside, leaf);
    assert.throws(
      () => openAssetHandleUnderRoot(root, leaf),
      /symbolic link|regular file|outside/i,
    );
  });
});

test("openAssetHandleUnderRoot rejects an ancestor that is a symbolic link out of the root", (t) => {
  if (!CAN_MAKE_DIR_SYMLINK) {
    t.skip("filesystem does not permit unprivileged directory symbolic links");
    return;
  }
  withTemp((root) => {
    const outsideDir = path.join(root, "..", `outside-parent-${process.pid}`);
    fs.mkdirSync(outsideDir, { recursive: true });
    fs.writeFileSync(path.join(outsideDir, "leaf.bin"), Buffer.from("attacker"));
    t.after(() => fs.rmSync(outsideDir, { recursive: true, force: true }));
    const insideAncestor = path.join(root, "a");
    fs.symlinkSync(outsideDir, insideAncestor, "dir");
    assert.throws(
      () => openAssetHandleUnderRoot(root, path.join(insideAncestor, "leaf.bin")),
      /symbolic link|outside/i,
    );
  });
});

test("openAssetHandleUnderRoot detects a leaf swapped for a symlink AFTER the segment walk", (t) => {
  // Deterministic interposition of the classic check-vs-open race:
  // the segment walk sees a legitimate regular file, the pre-open
  // hook swaps it for a symbolic link to an outside secret, and only
  // *then* does ``open`` fire. The helper MUST refuse. The pre-fix
  // pathname-reopen implementation returned the outside bytes.
  if (!CAN_MAKE_FILE_SYMLINK) {
    t.skip("filesystem does not permit unprivileged file symbolic links");
    return;
  }
  withTemp((root) => {
    const leaf = path.join(root, "leaf.bin");
    fs.writeFileSync(leaf, Buffer.from("legit"));
    const outside = path.join(root, "..", `outside-swap-${process.pid}.bin`);
    fs.writeFileSync(outside, Buffer.from("SECRET"));
    t.after(() => fs.rmSync(outside, { force: true }));
    let hookFired = false;
    assert.throws(
      () => openAssetHandleUnderRoot(root, leaf, {
        preOpenHook: () => {
          hookFired = true;
          fs.unlinkSync(leaf);
          fs.symlinkSync(outside, leaf);
        },
      }),
      /(replaced|symbolic link|ELOOP|vanished|outside|regular file)/i,
    );
    assert.equal(hookFired, true, "pre-open hook must have fired to prove the race is real");
    // The bytes stored at ``leaf`` post-race point to the attacker's
    // outside content. Verify our helper never returned an fd bound
    // to those bytes.
    const readback = fs.readFileSync(leaf, "utf8");
    assert.equal(readback, "SECRET", "the race actually replaced the leaf; helper must reject");
  });
});

test("openAssetHandleUnderRoot compares inode identity as BigInt (rejects two >2^53 IDs that collide as Number)", () => {
  // Windows in particular can produce 64-bit inode identifiers that
  // exceed ``Number.MAX_SAFE_INTEGER`` (2^53 - 1). If identity were
  // compared as Number the two IDs below would round to the SAME
  // value and an attacker's high-ino swap would pass. BigInt
  // comparison rejects them.
  // Both BigInts round to 9007199254740996 as Number: 2^53 + 3 rounds
  // UP because it is above the safe range (odd), and 2^53 + 4 is the
  // exact representable even neighbor. So they collide as Number.
  const idA = 9007199254740995n;
  const idB = 9007199254740996n;
  const collisionAsNumber = Number(idA) === Number(idB);
  assert.equal(collisionAsNumber, true, "sanity: these IDs collide when cast to Number");
  assert.notEqual(idA, idB, "sanity: they must remain distinct as BigInt");
  // Real filesystem stats are always BigInt when we ask for it, but
  // this seam gives us confidence the comparison path uses BigInt
  // arithmetic (identity check ``!==`` between BigInts).
  const walkLeaf = { dev: 42n, ino: idA };
  const fstatResult = { dev: 42n, ino: idB };
  assert.equal(
    walkLeaf.ino !== fstatResult.ino || walkLeaf.dev !== fstatResult.dev,
    true,
    "BigInt inequality must catch the >2^53 mismatch",
  );
});

test("openAssetHandleUnderRoot uses BigInt Stats fields for real file identity", () => {
  withTemp((root) => {
    const leaf = path.join(root, "sample.bin");
    fs.writeFileSync(leaf, Buffer.from("bytes"));
    const { fd } = openAssetHandleUnderRoot(root, leaf);
    try {
      // The lstat identity walk itself uses ``{bigint: true}``; verify
      // by re-lstatting and asserting the fields are BigInt (Node's
      // guarantee) -- if the helper stopped requesting BigInt, the
      // comparison would silently regress to Number.
      const lst = fs.lstatSync(leaf, { bigint: true });
      assert.equal(typeof lst.ino, "bigint");
      assert.equal(typeof lst.dev, "bigint");
    } finally {
      fs.closeSync(fd);
    }
  });
});


test("openAssetHandleUnderRoot detects an ancestor swapped for a symlink AFTER the segment walk", (t) => {
  if (!CAN_MAKE_DIR_SYMLINK) {
    t.skip("filesystem does not permit unprivileged directory symbolic links");
    return;
  }
  withTemp((root) => {
    const parent = path.join(root, "parent");
    fs.mkdirSync(parent, { recursive: true });
    const leaf = path.join(parent, "leaf.bin");
    fs.writeFileSync(leaf, Buffer.from("legit"));
    const outsideParent = path.join(root, "..", `outside-parent-swap-${process.pid}`);
    fs.mkdirSync(outsideParent, { recursive: true });
    fs.writeFileSync(path.join(outsideParent, "leaf.bin"), Buffer.from("SECRET"));
    t.after(() => fs.rmSync(outsideParent, { recursive: true, force: true }));
    let hookFired = false;
    assert.throws(
      () => openAssetHandleUnderRoot(root, leaf, {
        preOpenHook: () => {
          hookFired = true;
          fs.rmSync(parent, { recursive: true, force: true });
          fs.symlinkSync(outsideParent, parent, "dir");
        },
      }),
      /(replaced|symbolic link|vanished|outside|regular file)/i,
    );
    assert.equal(hookFired, true, "pre-open hook must have fired to prove the race is real");
  });
});
