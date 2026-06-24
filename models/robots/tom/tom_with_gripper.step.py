from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


V2_DIR = Path(__file__).resolve().parent
TOM_SOURCE = V2_DIR / "tom.step.py"


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
    tom_module = _load_tom_module()
    # tom.py's import side effects put V2_DIR on sys.path, so robot_common imports here.
    from robot_common.link_assembly import compound_from_instances

    envelope = tom_module.gen_step_with_options(include_gripper=True)
    return {
        "shape": compound_from_instances(
            "tom_with_gripper",
            envelope["instances"],
            base_dir=V2_DIR,
            assembly_mates=envelope.get("assembly_mates", []),
        ),
    }


def gen_urdf() -> dict[str, object]:
    return _load_tom_module().gen_urdf_with_options(
        include_gripper=True,
        robot_name="tom_v2_with_gripper",
        source_name="models/robots/tom/tom_with_gripper.py",
    )


def gen_srdf() -> dict[str, object]:
    return _load_tom_module().gen_srdf_with_options(
        include_gripper=True,
        robot_name="tom_v2_with_gripper",
        urdf="tom_with_gripper.urdf",
    )
