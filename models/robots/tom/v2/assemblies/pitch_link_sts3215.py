from __future__ import annotations


YOKE_PLATE_FACE_X_MM = -57.5
YOKE_HORN_AXIS_Y_MM = -9.1
YOKE_HORN_AXIS_Z_MM = 0.0
# The horn's outer contact face seats directly on the outside yoke plate face.
YOKE_HORN_MATING_FACE_X_MM = YOKE_PLATE_FACE_X_MM
# Use the output horn face against the yoke plate so the servo's case-bottom
# cable face points toward the next roll-link bracket instead of away from it.
STS3215_OUTPUT_HORN_AXIS_LOCAL_X_MM = -25.5
STS3215_OUTPUT_HORN_FACE_LOCAL_Y_MM = 9.2
MATE_TOLERANCE_MM = 1e-6

STS3215_TRANSFORM = [
    0.0,
    1.0,
    0.0,
    (
        YOKE_HORN_MATING_FACE_X_MM
        - STS3215_OUTPUT_HORN_FACE_LOCAL_Y_MM
    ),
    -1.0,
    0.0,
    0.0,
    YOKE_HORN_AXIS_Y_MM + STS3215_OUTPUT_HORN_AXIS_LOCAL_X_MM,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _validate_sts3215_transform() -> None:
    horn_face_x = (
        STS3215_TRANSFORM[1] * STS3215_OUTPUT_HORN_FACE_LOCAL_Y_MM
        + STS3215_TRANSFORM[3]
    )
    if abs(horn_face_x - YOKE_HORN_MATING_FACE_X_MM) > MATE_TOLERANCE_MM:
        raise RuntimeError(
            "STS3215 output horn face is not seated on the yoke horn mating face: "
            f"{horn_face_x:.6f} != {YOKE_HORN_MATING_FACE_X_MM:.6f}"
        )
    horn_axis_y = (
        STS3215_TRANSFORM[4] * STS3215_OUTPUT_HORN_AXIS_LOCAL_X_MM
        + STS3215_TRANSFORM[7]
    )
    if abs(horn_axis_y - YOKE_HORN_AXIS_Y_MM) > MATE_TOLERANCE_MM:
        raise RuntimeError(
            "STS3215 output horn axis is not centered on the yoke horn face: "
            f"{horn_axis_y:.6f} != {YOKE_HORN_AXIS_Y_MM:.6f}"
        )
    horn_axis_z = STS3215_TRANSFORM[11]
    if abs(horn_axis_z - YOKE_HORN_AXIS_Z_MM) > MATE_TOLERANCE_MM:
        raise RuntimeError(
            "STS3215 output horn axis Z is not centered on the yoke horn face: "
            f"{horn_axis_z:.6f} != {YOKE_HORN_AXIS_Z_MM:.6f}"
        )


def gen_step() -> dict[str, object]:
    _validate_sts3215_transform()
    return {
        "instances": [
            {
                "path": "../servo_horn_yoke.step",
                "name": "servo_horn_yoke",
                "transform": [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            },
            {
                "path": "../imports/sts3215.step",
                "name": "sts3215",
                "transform": STS3215_TRANSFORM,
            },
        ],
    }
