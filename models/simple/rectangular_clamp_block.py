# Prompt: Rectangular clamp block with a split slot and two transverse screw holes.

from build123d import Location

from simple_model_library import make_rectangular_clamp_block


def gen_step():
    world_part = make_rectangular_clamp_block()
    centered = world_part.moved(Location((0.0, 0.0, -14.0)))
    return centered.scale(1.0 / 70.0)
