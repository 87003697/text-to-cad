from __future__ import annotations


YOKE_PLATE_FACE_X_MM = -57.5
STS3250_YOKE_PLATE_FACE_LOCAL_Y_MM = -27.4

STS3250_TRANSFORM = [
    0.0,
    -1.0,
    0.0,
    (
        YOKE_PLATE_FACE_X_MM
        + STS3250_YOKE_PLATE_FACE_LOCAL_Y_MM
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
                "path": "../parts/imports/sts3250.step",
                "name": "sts3250",
                "transform": STS3250_TRANSFORM,
            },
        ],
    }
