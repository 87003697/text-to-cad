from pathlib import Path

from cadgen.step_scene import import_step


def gen_step():
    return {
        "shape": import_step(Path(__file__).parent / "180_degree_flip_mechanism.step"),
        "params": "180_degree_flip_mechanism.params.js",
    }
