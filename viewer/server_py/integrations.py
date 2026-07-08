"""Local "Open in <CAD app>" integrations: detection, launch, and reveal.

Serves GET /__cad/integrations (which allowlisted desktop CAD apps are installed
on this machine) and the launch half of POST /__cad/open-in and POST /__cad/reveal.
Everything is a subprocess/stdlib probe (no pyobjc/winreg deps beyond the stdlib),
mirroring save_dialog.py's per-platform-function + graceful-degradation shape.

Security invariants (the request layer relies on these):
- The client only ever names an ``appId``; the executable/bundle and argv shape
  come from the server-side allowlist below. Arbitrary app names/paths are never
  accepted.
- The file argument must be an absolute, existing ``.step``/``.stp`` path. Values
  starting with ``-`` or containing ``://`` are rejected before any spawn: macOS
  ``open`` treats URL-looking strings as scheme launches and leading dashes as
  flags (CWE-88 argument injection).
- Spawns use argument vectors, never a shell.

Env seams (tests/headless, same pattern as VIEWER_SAVE_DIALOG_FORCE_PATH):
- ``VIEWER_OPEN_IN_PLATFORM``: force "darwin" | "win32" | "linux" recipes.
- ``VIEWER_OPEN_IN_FORCE_INSTALLED``: comma list of app ids to report installed,
  each optionally ``id=/detected/path``.
- ``VIEWER_DISABLE_APP_LAUNCH=1``: never spawn; return the argv that would run.

FreeCAD gotcha encoded below: ``open -a FreeCAD file.step`` silently drops the
file (its Apple-event handler only honors .fcstd), so FreeCAD launches exec the
bundle binary directly with ``--single-instance``.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
from urllib.parse import quote

STEP_OPEN_EXTENSIONS = (".step", ".stp")

_FUSION_WEBDEPLOY_DARWIN = "~/Library/Application Support/Autodesk/webdeploy/production"


def integration_platform() -> str:
    forced = str(os.environ.get("VIEWER_OPEN_IN_PLATFORM") or "").strip().lower()
    if forced in ("darwin", "win32", "linux"):
        return forced
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def _first_existing_dir(*candidates):
    for candidate in candidates:
        path = _expand(candidate)
        if os.path.isdir(path):
            return path
    return ""


def _first_existing_file(*candidates):
    for candidate in candidates:
        path = _expand(candidate)
        if os.path.isfile(path):
            return path
    return ""


def _first_glob_file(*patterns):
    for pattern in patterns:
        matches = sorted(glob.glob(_expand(pattern)), reverse=True)
        for match in matches:
            if os.path.isfile(match):
                return match
    return ""


def _which(*names):
    from shutil import which

    for name in names:
        found = which(name)
        if found:
            return found
    return ""


def _win_registry_value(root_name: str, key_path: str, value_name: str = ""):
    try:
        import winreg
    except ImportError:
        return ""
    root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT}.get(root_name)
    if root is None:
        return ""
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return str(value or "").strip().strip('"')
    except OSError:
        return ""


def _win_registry_key_exists(root_name: str, key_path: str) -> bool:
    try:
        import winreg
    except ImportError:
        return False
    root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT}.get(root_name)
    if root is None:
        return False
    try:
        with winreg.OpenKey(root, key_path):
            return True
    except OSError:
        return False


def _win_solidworks_setup_exe():
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SolidWorks") as parent:
            index = 0
            years = []
            while True:
                try:
                    name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                if name.upper().startswith("SOLIDWORKS 2"):
                    years.append(name)
            for name in sorted(years, reverse=True):
                folder = _win_registry_value("HKLM", rf"SOFTWARE\SolidWorks\{name}\Setup", "SolidWorks Folder")
                if folder:
                    exe = os.path.join(folder, "SLDWORKS.exe")
                    if os.path.isfile(exe):
                        return exe
    except OSError:
        pass
    return ""


# --- per-app probes: return the detected app path/exe ("" when not installed) ---
def _probe_freecad_darwin():
    return _first_existing_dir("/Applications/FreeCAD.app", "~/Applications/FreeCAD.app")


def _probe_freecad_win32():
    return (
        _win_registry_value("HKLM", r"Software\Microsoft\Windows\CurrentVersion\App Paths\FreeCAD.exe")
        or _win_registry_value("HKCU", r"Software\Microsoft\Windows\CurrentVersion\App Paths\FreeCAD.exe")
        or _first_glob_file(r"C:\Program Files\FreeCAD*\bin\FreeCAD.exe",
                            r"%LOCALAPPDATA%\Programs\FreeCAD*\bin\FreeCAD.exe")
    )


def _probe_fusion_darwin():
    # Fusion installs under the per-user webdeploy tree, NOT /Applications, and
    # Spotlight does not index ~/Library — stat the known locations directly.
    return _first_existing_dir(
        "~/Applications/Autodesk Fusion.app",
        "~/Applications/Autodesk Fusion 360.app",
        os.path.join(_FUSION_WEBDEPLOY_DARWIN, "Autodesk Fusion 360.app"),
        _FUSION_WEBDEPLOY_DARWIN,
    )


def _probe_fusion_win32():
    if _win_registry_key_exists("HKCU", r"Software\Classes\fusion360"):
        return "fusion360"
    if _first_existing_dir(r"%LOCALAPPDATA%\Autodesk\webdeploy\production"):
        return "fusion360"
    return ""


def _probe_rhino_darwin():
    return _first_existing_dir("/Applications/Rhino 8.app", "/Applications/Rhinoceros.app")


def _probe_rhino_win32():
    install = _win_registry_value("HKLM", r"SOFTWARE\McNeel\Rhinoceros\8.0\Install", "Path")
    if install:
        exe = os.path.join(install, "System", "Rhino.exe")
        if os.path.isfile(exe):
            return exe
    return _first_glob_file(r"C:\Program Files\Rhino 8\System\Rhino.exe")


def _probe_edrawings_darwin():
    return _first_existing_dir("/Applications/eDrawings.app")


def _probe_edrawings_win32():
    return _first_glob_file(r"C:\Program Files\SOLIDWORKS Corp\eDrawings*\eDrawings.exe",
                            r"C:\Program Files\Common Files\eDrawings*\eDrawings.exe")


def _probe_cad_assistant_darwin():
    return _first_existing_dir("/Applications/CAD Assistant.app", "/Applications/CADAssistant.app")


def _probe_shapr3d_darwin():
    return _first_existing_dir("/Applications/Shapr3D.app")


def _probe_plasticity_darwin():
    return _first_existing_dir("/Applications/Plasticity.app")


def _probe_plasticity_win32():
    return _first_glob_file(r"%LOCALAPPDATA%\Programs\Plasticity\Plasticity.exe")


# --- launch command builders: (detected, file_path) -> argv ---
def _open_with_bundle(detected, file_path):
    return ["open", "-a", detected, file_path]


def _freecad_darwin_command(detected, file_path):
    return [os.path.join(detected, "Contents", "MacOS", "FreeCAD"), "--single-instance", file_path]


def _single_instance_exe_command(detected, file_path):
    return [detected, "--single-instance", file_path]


def fusion_open_url(file_path: str) -> str:
    return "fusion360://host/?command=open&file=" + quote(file_path, safe="")


def _fusion_darwin_command(detected, file_path):
    del detected
    return ["open", fusion_open_url(file_path)]


def _fusion_win32_command(detected, file_path):
    del detected
    return ["os.startfile", fusion_open_url(file_path)]


def _exe_file_command(detected, file_path):
    return [detected, file_path]


def _system_default_darwin(detected, file_path):
    del detected
    return ["open", file_path]


def _system_default_win32(detected, file_path):
    del detected
    return ["os.startfile", file_path]


def _system_default_linux(detected, file_path):
    del detected
    return ["xdg-open", file_path]


def _always(_value="system"):
    return lambda: _value


APP_INTEGRATIONS = (
    {
        "id": "freecad",
        "name": "FreeCAD",
        "platforms": {
            "darwin": {"probe": _probe_freecad_darwin, "command": _freecad_darwin_command},
            "win32": {"probe": _probe_freecad_win32, "command": _single_instance_exe_command},
            "linux": {"probe": lambda: _which("freecad"), "command": _single_instance_exe_command},
        },
    },
    {
        "id": "fusion360",
        "name": "Autodesk Fusion",
        "platforms": {
            "darwin": {"probe": _probe_fusion_darwin, "command": _fusion_darwin_command},
            "win32": {"probe": _probe_fusion_win32, "command": _fusion_win32_command},
        },
    },
    {
        "id": "rhino",
        "name": "Rhino 8",
        "platforms": {
            "darwin": {"probe": _probe_rhino_darwin, "command": _open_with_bundle},
            "win32": {"probe": _probe_rhino_win32, "command": _exe_file_command},
        },
    },
    {
        "id": "shapr3d",
        "name": "Shapr3D",
        "platforms": {
            "darwin": {"probe": _probe_shapr3d_darwin, "command": _open_with_bundle},
        },
    },
    {
        "id": "plasticity",
        "name": "Plasticity",
        "platforms": {
            "darwin": {"probe": _probe_plasticity_darwin, "command": _open_with_bundle},
            "win32": {"probe": _probe_plasticity_win32, "command": _exe_file_command},
            "linux": {"probe": lambda: _which("plasticity"), "command": _exe_file_command},
        },
    },
    {
        "id": "edrawings",
        "name": "eDrawings",
        "platforms": {
            "darwin": {"probe": _probe_edrawings_darwin, "command": _open_with_bundle},
            "win32": {"probe": _probe_edrawings_win32, "command": _exe_file_command},
        },
    },
    {
        "id": "cad-assistant",
        "name": "CAD Assistant",
        "platforms": {
            "darwin": {"probe": _probe_cad_assistant_darwin, "command": _open_with_bundle},
        },
    },
    {
        "id": "solidworks",
        "name": "SOLIDWORKS",
        "platforms": {
            "win32": {"probe": _win_solidworks_setup_exe, "command": _exe_file_command},
        },
    },
    {
        "id": "system-default",
        "name": "System Default",
        "platforms": {
            "darwin": {"probe": _always(), "command": _system_default_darwin},
            "win32": {"probe": _always(), "command": _system_default_win32},
            "linux": {"probe": _always(), "command": _system_default_linux},
        },
    },
)


def _forced_installed() -> dict:
    forced = {}
    raw = str(os.environ.get("VIEWER_OPEN_IN_FORCE_INSTALLED") or "").strip()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        app_id, _, detected = token.partition("=")
        forced[app_id.strip()] = detected.strip() or "forced"
    return forced


def _detect(app: dict, platform: str, forced: dict) -> str:
    if app["id"] in forced:
        return forced[app["id"]]
    spec = app["platforms"].get(platform)
    if not spec:
        return ""
    try:
        return str(spec["probe"]() or "")
    except Exception:  # noqa: BLE001 — a broken probe means "not installed", never a 500
        return ""


def list_integration_targets() -> dict:
    platform = integration_platform()
    forced = _forced_installed()
    targets = []
    for app in APP_INTEGRATIONS:
        if platform not in app["platforms"]:
            continue
        targets.append({
            "id": app["id"],
            "name": app["name"],
            "installed": bool(_detect(app, platform, forced)),
        })
    return {"platform": platform, "targets": targets}


def validate_open_target_path(file_path: str) -> str:
    path = str(file_path or "").strip()
    if not path or not os.path.isabs(path):
        raise ValueError("Open in app requires an absolute STEP file path")
    if path.startswith("-") or "://" in path:
        raise ValueError("Invalid STEP file path")
    resolved = os.path.realpath(path)
    if os.path.splitext(resolved)[1].lower() not in STEP_OPEN_EXTENSIONS:
        raise ValueError("Only STEP/STP files can be opened in a CAD app")
    if not os.path.isfile(resolved):
        raise ValueError(f"STEP file not found: {resolved}")
    return resolved


def _spawn_detached(argv) -> None:
    if argv and argv[0] == "os.startfile":
        os.startfile(argv[1])  # noqa: PTH — Windows-only ShellExecute wrapper
        return
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)  # noqa: S603 — argv comes from the server-side allowlist


def launch_integration(app_id: str, file_path: str) -> dict:
    """Open ``file_path`` in the allowlisted app. Returns
    ``{ok, appId, appName, command?}`` (command only when spawning is disabled)."""
    normalized_id = str(app_id or "").strip()
    app = next((a for a in APP_INTEGRATIONS if a["id"] == normalized_id), None)
    if not app:
        raise ValueError(f"Unknown Open-in app: {app_id or '(missing)'}")
    platform = integration_platform()
    spec = app["platforms"].get(platform)
    if not spec:
        raise ValueError(f"{app['name']} is not available on this platform")
    detected = _detect(app, platform, _forced_installed())
    if not detected:
        raise ValueError(f"{app['name']} is not installed")
    resolved = validate_open_target_path(file_path)
    argv = spec["command"](detected, resolved)
    payload = {"ok": True, "appId": app["id"], "appName": app["name"]}
    if os.environ.get("VIEWER_DISABLE_APP_LAUNCH") == "1":
        payload["command"] = list(argv)
        return payload
    try:
        _spawn_detached(argv)
    except OSError as exc:
        raise ValueError(f"Failed to launch {app['name']}: {exc}") from exc
    return payload


def reveal_command(file_path: str, platform: str = "") -> list:
    platform = platform or integration_platform()
    if platform == "darwin":
        return ["open", "-R", file_path]
    if platform == "win32":
        return ["explorer.exe", f"/select,{file_path}"]
    return ["xdg-open", os.path.dirname(file_path)]


def reveal_in_file_manager(file_path: str) -> dict:
    path = str(file_path or "").strip()
    if not path or not os.path.isabs(path) or path.startswith("-") or "://" in path:
        raise ValueError("Reveal requires an absolute file path")
    resolved = os.path.realpath(path)
    if not os.path.exists(resolved):
        raise ValueError(f"File not found: {resolved}")
    argv = reveal_command(resolved)
    if os.environ.get("VIEWER_DISABLE_APP_LAUNCH") == "1":
        return {"ok": True, "command": list(argv)}
    try:
        _spawn_detached(argv)
    except OSError as exc:
        raise ValueError(f"Failed to reveal file: {exc}") from exc
    return {"ok": True}
