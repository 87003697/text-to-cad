from __future__ import annotations


SOURCE_REF = "@cad[STEP/robot_arm#o1.19]"

BASE_PLATE_TOP_Y_MM = -29.8002
BASE_PLATE_THICKNESS_MM = 25.4 * 0.080
BASE_PLATE_BOTTOM_Y_MM = BASE_PLATE_TOP_Y_MM - BASE_PLATE_THICKNESS_MM
BASE_LINK_FLOOR_Y_OFFSET_MM = -BASE_PLATE_BOTTOM_Y_MM
STS3250_BASE_PLATE_FACE_LOCAL_Y_MM = -27.4
STS3250_BASE_PLATE_FLUSH_Y_OFFSET_MM = (
    BASE_PLATE_TOP_Y_MM
    - STS3250_BASE_PLATE_FACE_LOCAL_Y_MM
)

STS3250_BASE_PLATE_TRANSFORM = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, BASE_LINK_FLOOR_Y_OFFSET_MM + STS3250_BASE_PLATE_FLUSH_Y_OFFSET_MM,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]

BASE_PLATE_TRANSFORM = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, BASE_LINK_FLOOR_Y_OFFSET_MM,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def gen_step() -> dict[str, object]:
    return {
        "instances": [
            {
                "path": "../imports/sts3250.step",
                "name": "sts3250_3",
                "transform": STS3250_BASE_PLATE_TRANSFORM,
            },
            {
                "path": "../base_plate.step",
                "name": "base_plate",
                "transform": BASE_PLATE_TRANSFORM,
            },
        ],
    }
