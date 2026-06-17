from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parents[1]
PARTS_DIR = V2_DIR / "parts"
for path in (V2_DIR, PARTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import servo_horn_yoke

# Demonstration assembly for the servo horn yoke's intended mate: the yoke
# straddles both the output horn and rear horn, with the yoke flipped 180 degrees
# about its web axis so the web remains outside the servo case.
YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM = -9.1
YOKE_HORN_FACE_CLEARANCE_MM = 0.0
YOKE_180_ABOUT_WEB_AXIS_TRANSFORM = list(
    servo_horn_yoke.STANDALONE_YOKE_ON_SERVO_HORNS_TRANSFORM
)


def gen_step() -> dict[str, object]:
    return {
        "instances": [
            {
                "path": "../parts/imports/sts3250.step",
                "name": "sts3250",
                "transform": [
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                ],
                "use_source_colors": True,
            },
            {
                "path": "../parts/servo_horn_yoke.step",
                "name": "servo_horn_yoke",
                "transform": YOKE_180_ABOUT_WEB_AXIS_TRANSFORM,
            },
        ],
        "assembly_mates": [
            {
                "sourceLabel": "yoke_rear_plate_to_output_horn",
                "type": "rigid",
                "relation": "rigid",
                "fixed": "sts3250:output_horn_face",
                "moving": "servo_horn_yoke:rear_horn_plate",
                "parameters": {"clearance_mm": YOKE_HORN_FACE_CLEARANCE_MM},
                "fixedEndpoint": {
                    "part": "sts3250",
                    "frame": "output_horn_face",
                },
                "movingEndpoint": {
                    "part": "servo_horn_yoke",
                    "frame": "rear_horn_plate",
                },
            },
            {
                "sourceLabel": "yoke_output_plate_to_rear_horn",
                "type": "rigid",
                "relation": "rigid",
                "fixed": "sts3250:rear_horn_face",
                "moving": "servo_horn_yoke:output_horn_plate",
                "parameters": {"clearance_mm": YOKE_HORN_FACE_CLEARANCE_MM},
                "fixedEndpoint": {
                    "part": "sts3250",
                    "frame": "rear_horn_face",
                },
                "movingEndpoint": {
                    "part": "servo_horn_yoke",
                    "frame": "output_horn_plate",
                },
            },
        ],
    }
