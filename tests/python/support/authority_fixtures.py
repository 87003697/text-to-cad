"""Reusable plugin-authority fixture builder for global tests.

Builds a minimally-realistic ``~/.text-to-cad-codex/deployments/<id>/`` slot
with a symlink-free publish tree carrying every ``CRITICAL_RUNTIME_PATHS``
probe file, an isolated ``codex-home/`` whose ``plugins/cache/`` mirror the
publish tree byte-for-byte, an ``on-disk`` deployment receipt, and the
atomically-published ``current.json`` pointer.

The output is realistic enough that ``plugin_deployment.resolve_current_authority``
succeeds end-to-end — publish-tree and installed-tree manifest recomputes, the
critical-runtime probes, and the lexical containment checks all pass without
mocks. Tests that want to exercise fail-closed paths mutate one specific piece
of state (add a symlink, tamper a digest, remove a probe) after the fixture is
built.

This is the single canonical place to construct authority test state so any
schema change in ``plugin_deployment`` mechanically breaks exactly one fixture.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.python.support.paths import REPO_ROOT


def _load_plugin_deployment():
    if "plugin_deployment" in sys.modules:
        return sys.modules["plugin_deployment"]
    module_path = REPO_ROOT / "scripts" / "pilot" / "plugin_deployment.py"
    spec = importlib.util.spec_from_file_location("plugin_deployment", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_smoke():
    if "smoke_installed_plugin" in sys.modules:
        return sys.modules["smoke_installed_plugin"]
    module_path = REPO_ROOT / "scripts" / "release" / "smoke_installed_plugin.py"
    spec = importlib.util.spec_from_file_location(
        "smoke_installed_plugin", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_deployment = _load_plugin_deployment()
smoke = _load_smoke()


@dataclass
class AuthorityFixture:
    """Handles to every path materialized by :func:`build_authority`."""

    codex_home_root: Path
    receipt: object  # plugin_deployment.DeploymentReceipt
    deployment_dir: Path
    publish_tree: Path
    codex_home: Path
    installed_path: Path


def _write_probe(root: Path, relative: str, payload: bytes = b"probe\n") -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _write_publish_body(publish_tree: Path, version: str, dedupe_token: str) -> None:
    """Populate a plausible publish tree covering every critical runtime probe."""

    (publish_tree / "VERSION").write_text(version + "\n", encoding="utf-8")
    (publish_tree / "AGENTS.md").write_text(
        f"# authority fixture {dedupe_token}\n", encoding="utf-8"
    )
    (publish_tree / ".codex-plugin").mkdir(exist_ok=True)
    (publish_tree / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "cad", "version": version}) + "\n", encoding="utf-8"
    )
    for runtime_dir, probe_rel in smoke.CRITICAL_RUNTIME_PATHS:
        _write_probe(
            publish_tree,
            f"{runtime_dir}/{probe_rel}",
            payload=f"{runtime_dir} {dedupe_token}\n".encode("utf-8"),
        )
    # Each critical runtime lives inside a skill directory; make the skill
    # visible to resolved_skill_directories by planting its SKILL.md.
    seen_skills: set[str] = set()
    for runtime_dir, _ in smoke.CRITICAL_RUNTIME_PATHS:
        skill_name = Path(runtime_dir).parts[1]
        if skill_name in seen_skills:
            continue
        seen_skills.add(skill_name)
        skill_dir = publish_tree / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"{skill_name}\n", encoding="utf-8")


def _clone_publish_into_cache(publish_tree: Path, installed_path: Path) -> None:
    """Copy the publish tree bytes into the installed cache without symlinks."""

    for src in publish_tree.rglob("*"):
        if src.is_symlink() or not src.is_file():
            continue
        rel = src.relative_to(publish_tree)
        dst = installed_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def build_authority(
    codex_home_root: Path,
    *,
    version: str = "0.4.21",
    dedupe_token: str = "default",
    codex_version: str = "codex-cli 0.147.0",
    transfer_provenance: dict | None = None,
    publish: bool = True,
) -> AuthorityFixture:
    """Build a resolveable authority slot rooted at ``codex_home_root``.

    Set ``publish=False`` to leave ``current.json`` unwritten (useful for
    fail-closed tests). ``dedupe_token`` lets a single test build two authority
    trees with distinct prepared digests without touching version identity.
    """

    provenance = transfer_provenance or {
        "schema": "text-to-cad.push-provenance/1",
        "mac_branch": "develop",
        "mac_head": "0" * 40,
        "mac_state": "clean",
        "transfer_summary": {
            "sent_bytes": 1,
            "received_bytes": 1,
            "bytes_per_second": 1.0,
        },
        "runtime_attestation": {"scripts/pilot/runner.py": "a" * 64},
    }
    deployments_root = plugin_deployment.ensure_authority_root(codex_home_root)

    staging = deployments_root / f".staging-{dedupe_token}"
    staging.mkdir()
    publish_tree_stage = staging / plugin_deployment.PUBLISH_TREE_DIRNAME
    codex_home_stage = staging / plugin_deployment.CODEX_HOME_DIRNAME
    publish_tree_stage.mkdir()
    codex_home_stage.mkdir()

    _write_publish_body(publish_tree_stage, version, dedupe_token)
    installed_rel = Path("plugins/cache/text-to-cad/cad") / version
    installed_stage = codex_home_stage / installed_rel
    installed_stage.mkdir(parents=True)
    _clone_publish_into_cache(publish_tree_stage, installed_stage)

    prepared = smoke.compute_manifest(publish_tree_stage)
    installed_manifest = smoke.compute_manifest(installed_stage)
    smoke.assert_manifests_equal(prepared, installed_manifest)
    critical_runtimes = smoke.assert_critical_runtimes(installed_stage)

    deployment_id = plugin_deployment.compute_deployment_id(prepared.digest, version)
    deployment_dir = plugin_deployment.deployment_directory(codex_home_root, deployment_id)
    final_publish_tree = deployment_dir / plugin_deployment.PUBLISH_TREE_DIRNAME
    final_codex_home = deployment_dir / plugin_deployment.CODEX_HOME_DIRNAME
    final_installed = final_codex_home / installed_rel

    (codex_home_stage / "config.toml").write_text(
        "[marketplaces.text-to-cad]\n"
        f'source = "{final_publish_tree}"\n'
        'source_type = "local"\n'
        '[plugins."cad@text-to-cad"]\n'
        "enabled = true\n",
        encoding="utf-8",
    )

    receipt = plugin_deployment.DeploymentReceipt(
        schema=plugin_deployment.RECEIPT_SCHEMA,
        deployment_id=deployment_id,
        version=version,
        plugin_selector="cad@text-to-cad",
        prepared_manifest_digest=prepared.digest,
        installed_manifest_digest=installed_manifest.digest,
        codex_version=codex_version,
        published_at="2026-08-23T00:00:00Z",
        source_git_sha="deadbeef" * 5,
        deployment_dir=deployment_dir,
        publish_tree=final_publish_tree,
        codex_home=final_codex_home,
        installed_path=final_installed,
        critical_runtimes=tuple(
            {str(k): str(v) for k, v in item.items()} for item in critical_runtimes
        ),
        transfer_provenance=provenance,
    )
    (staging / plugin_deployment.RECEIPT_FILE).write_text(
        json.dumps(receipt.as_dict(), sort_keys=True) + "\n", encoding="utf-8"
    )
    plugin_deployment.move_into_place(staging, deployment_dir)
    if publish:
        plugin_deployment.publish_authority(receipt, codex_home_root=codex_home_root)
    return AuthorityFixture(
        codex_home_root=codex_home_root,
        receipt=receipt,
        deployment_dir=deployment_dir,
        publish_tree=final_publish_tree,
        codex_home=final_codex_home,
        installed_path=final_installed,
    )
