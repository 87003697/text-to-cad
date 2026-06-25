// Native OS "Save As" dialog for the local CAD Viewer server.
//
// The viewer is a browser app served by a local Node server, so there is no built-in OS
// file picker. Since the server runs on the user's own machine (127.0.0.1) it can spawn the
// platform's native save dialog directly — the same approach the existing reveal action uses
// with spawn("open"). No npm dependency: no maintained cross-platform save-dialog package
// exists, and the per-OS tools (osascript / zenity|kdialog / PowerShell) are what such a
// package would shell out to anyway.
//
// pickSaveDestination(...) resolves to exactly one of:
//   { path }            — the absolute destination the user chose
//   { cancelled: true } — the user dismissed the dialog
//   { unsupported: true } — no dialog tool is available (e.g. a headless server); the caller
//                           falls back to a plain browser download.

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

function runCapture(command, args) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    } catch (error) {
      resolve({ spawnError: error });
      return;
    }
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => resolve({ spawnError: error }));
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

async function pickDarwin({ prompt, suggestedName, defaultDir }) {
  // Drive osascript via `on run argv` so the prompt/name/dir are passed as arguments, never
  // interpolated into the script text (no quoting/escaping pitfalls).
  const script = [
    "on run argv",
    "set thePrompt to item 1 of argv",
    "set theName to item 2 of argv",
    "set theDir to item 3 of argv",
    "if theDir is not \"\" then",
    "set chosen to (choose file name with prompt thePrompt default name theName default location (POSIX file theDir))",
    "else",
    "set chosen to (choose file name with prompt thePrompt default name theName)",
    "end if",
    "return POSIX path of chosen",
    "end run",
  ];
  const args = [];
  for (const line of script) {
    args.push("-e", line);
  }
  args.push(prompt, suggestedName, defaultDir || "");
  const { code, stdout, stderr, spawnError } = await runCapture("osascript", args);
  if (spawnError) {
    return { unsupported: true };
  }
  if (code === 0) {
    const chosen = stdout.trim();
    return chosen ? { path: chosen } : { cancelled: true };
  }
  // Exit -128 / "User canceled" means dismissed; any other dialog error → nothing written.
  return { cancelled: true, message: stderr.trim() };
}

async function pickLinux({ suggestedName, defaultDir }) {
  const startPath = defaultDir ? path.join(defaultDir, suggestedName) : suggestedName;
  const zenity = await runCapture("zenity", [
    "--file-selection",
    "--save",
    "--confirm-overwrite",
    `--filename=${startPath}`,
  ]);
  if (!zenity.spawnError) {
    if (zenity.code === 0) {
      const chosen = zenity.stdout.trim();
      return chosen ? { path: chosen } : { cancelled: true };
    }
    return { cancelled: true };
  }
  const kdialog = await runCapture("kdialog", ["--getsavefilename", startPath]);
  if (!kdialog.spawnError) {
    if (kdialog.code === 0) {
      const chosen = kdialog.stdout.trim();
      return chosen ? { path: chosen } : { cancelled: true };
    }
    return { cancelled: true };
  }
  return { unsupported: true };
}

async function pickWindows({ suggestedName, defaultDir }) {
  const quote = (value) => String(value).replace(/'/g, "''");
  const command = [
    "Add-Type -AssemblyName System.Windows.Forms;",
    "$dialog = New-Object System.Windows.Forms.SaveFileDialog;",
    defaultDir ? `$dialog.InitialDirectory = '${quote(defaultDir)}';` : "",
    `$dialog.FileName = '${quote(suggestedName)}';`,
    "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.FileName) }",
  ]
    .filter(Boolean)
    .join(" ");
  const { code, stdout, spawnError } = await runCapture("powershell", [
    "-NoProfile",
    "-STA",
    "-Command",
    command,
  ]);
  if (spawnError) {
    return { unsupported: true };
  }
  if (code === 0) {
    const chosen = stdout.trim();
    return chosen ? { path: chosen } : { cancelled: true };
  }
  return { cancelled: true };
}

export async function pickSaveDestination({
  suggestedName = "export",
  defaultDir = "",
  prompt = "Export model as:",
} = {}) {
  // Test/automation hook: force a fixed destination (or cancellation) without popping a dialog.
  const forcedPath = String(process.env.VIEWER_SAVE_DIALOG_FORCE_PATH || "").trim();
  if (forcedPath) {
    return forcedPath === "__cancel__" ? { cancelled: true } : { path: forcedPath };
  }
  if (process.env.VIEWER_DISABLE_NATIVE_SAVE_DIALOG === "1") {
    return { unsupported: true };
  }
  const safeDir = defaultDir && fs.existsSync(defaultDir) ? defaultDir : "";
  try {
    if (process.platform === "darwin") {
      return await pickDarwin({ prompt, suggestedName, defaultDir: safeDir });
    }
    if (process.platform === "win32") {
      return await pickWindows({ suggestedName, defaultDir: safeDir });
    }
    return await pickLinux({ suggestedName, defaultDir: safeDir });
  } catch {
    return { unsupported: true };
  }
}
