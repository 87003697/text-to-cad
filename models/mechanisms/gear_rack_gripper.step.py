from pathlib import Path

from cadpy.step_scene import import_step


def gen_step():
    return {
        "shape": import_step(Path(__file__).parent / "gear_rack_gripper.step"),
        "params": "gear_rack_gripper.params.js",
    }
