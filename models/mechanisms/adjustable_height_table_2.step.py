from pathlib import Path

from cadgen.step_scene import import_step


def gen_step():
    return {
        "shape": import_step(Path(__file__).parent / "adjustable_height_table_2.step"),
        "params": "adjustable_height_table_2.params.js",
    }
