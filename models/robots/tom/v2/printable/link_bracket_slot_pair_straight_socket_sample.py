#!/usr/bin/env python3
"""Matching female dovetail half for the printable straight tongue shell."""

from __future__ import annotations

import sys
from pathlib import Path

PRINTABLE_DIR = Path(__file__).resolve().parent
if str(PRINTABLE_DIR) not in sys.path:
    sys.path.insert(0, str(PRINTABLE_DIR))

import link_bracket_slot_pair_straight_sample as connector_shell


PART_NAME = Path(__file__).stem


def build_step():
    shell = connector_shell.build_step(dovetail_role="female")
    shell.label = PART_NAME
    shell.color = connector_shell.PLASTIC_COLOR
    return shell


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
