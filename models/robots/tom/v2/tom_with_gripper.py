from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


V2_DIR = Path(__file__).resolve().parent
TOM_SOURCE = V2_DIR / "tom.py"


def _load_tom_module():
    module_name = f"_tom_v2_with_gripper_source_{abs(hash(TOM_SOURCE))}"
    spec = importlib.util.spec_from_file_location(module_name, TOM_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {TOM_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def gen_step() -> dict[str, object]:
    envelope = _load_tom_module().gen_step_with_options(include_gripper=True)
    return {
        "instances": envelope["instances"],
        "assembly_mates": envelope.get("assembly_mates", []),
    }


def gen_urdf() -> dict[str, object]:
    return _load_tom_module().gen_urdf_with_options(
        include_gripper=True,
        robot_name="tom_v2_with_gripper",
        source_name="models/robots/tom/v2/tom_with_gripper.py",
    )
