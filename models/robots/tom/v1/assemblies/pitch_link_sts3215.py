from __future__ import annotations


YOKE_PLATE_FACE_X_MM = -57.5
STS3215_YOKE_PLATE_FACE_LOCAL_Y_MM = -27.4

STS3215_TRANSFORM = [
    0.0,
    -1.0,
    0.0,
    (
        YOKE_PLATE_FACE_X_MM
        + STS3215_YOKE_PLATE_FACE_LOCAL_Y_MM
    ),
    1.0, 0.0, 0.0, 16.4,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def gen_step() -> dict[str, object]:
    return {
        "instances": [
            {
                "path": "../parts/servo_horn_yoke.step",
                "name": "servo_horn_yoke",
                "transform": [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            },
            {
                "path": "../parts/imports/sts3215.step",
                "name": "sts3215",
                "transform": STS3215_TRANSFORM,
            },
        ],
    }
