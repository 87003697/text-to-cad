from __future__ import annotations


# Demonstration assembly for the servo horn yoke's intended mate: the yoke
# straddles both the output horn and rear horn, with the yoke flipped 180 degrees
# about its web axis so the web remains outside the servo case.
YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM = -9.1
YOKE_180_ABOUT_WEB_AXIS_TRANSFORM = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    0.0,
    2.0 * YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM,
    0.0,
    0.0,
    -1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


def gen_step() -> dict[str, object]:
    return {
        "instances": [
            {
                "path": "../imports/sts3250.step",
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
                "path": "../servo_horn_yoke.step",
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
                "parameters": {"clearance_mm": 0.25},
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
                "parameters": {"clearance_mm": 0.25},
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
