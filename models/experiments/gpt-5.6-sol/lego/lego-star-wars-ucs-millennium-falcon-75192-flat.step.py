from pathlib import Path

from build123d import Location
from cadgen.step_scene import import_step


SOURCE_STEP = "lego-star-wars-ucs-millennium-falcon-75192.step"

# Rotate the source assembly onto the XY plane. The 98.4 mm Z translation
# places the bottom of source occurrence o1.296.1 (generated as
# o1.1.1.296.1) at z=0. The Y translation preserves the established flat
# model framing used by the animation sidecar.
FLAT_LOCATION = Location(
    (0.0, 308.55231958365, 98.4),
    (90.0, 0.0, 0.0),
)


def gen_step():
    shape = import_step(Path(__file__).parent / SOURCE_STEP)
    shape.location = FLAT_LOCATION
    return {
        "shape": shape,
        "params": "lego-star-wars-ucs-millennium-falcon-75192-flat.params.js",
    }
