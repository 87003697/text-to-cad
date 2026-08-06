"""Movement base entry: main plate, barrel + winding layer, going train,
bridges, escapement and balance of the caliber-321-lineage movement.

Movement local frame (see `_spec.py`): bridge side up, z = 0 at the main
plate's bridge-side surface.
"""

from build123d import Compound

import _mvt_base as M


def gen_step():
    return Compound(children=M.build_base(), label="movement_base")
