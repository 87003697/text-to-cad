"""Case cluster entry: every case part positioned in the watch frame.

z = 0 at the case-middle / caseback joint plane, +Z through the crystal,
crown at +X (see `_spec`).
"""

from build123d import Compound

import _case as C


def gen_step():
    return Compound(children=C.build_case_parts(), label="case")
