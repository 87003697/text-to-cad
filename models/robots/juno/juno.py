"""juno — compact humanoid robotics platform concept.

A sleek research humanoid with Unitree-G1-like proportions: ~1.40 m tall,
athletic stance, exposed cylindrical actuator modules at every joint,
warm-porcelain composite shells over graphite structure with machined
aluminum joint rims, coral accents on repeated functional details, a gloss
midnight-blue sensor visor with cyan pixel-grid eyes, and dexterous
five-digit hands. No logos.

Degrees of freedom (27 body DOF, statically posed):
  - each leg (x2): hip yaw, hip roll, hip pitch, knee pitch,
    ankle pitch, ankle roll                                   -> 12
  - each arm (x2): shoulder pitch, shoulder roll, shoulder yaw,
    elbow pitch, wrist roll, wrist pitch                      -> 12
  - waist yaw                                                 -> 1
  - neck yaw, neck pitch                                      -> 2
  Hands add cosmetic posed finger articulation (not counted).

Coordinates: pelvis waist-yaw joint center = world origin, +X forward,
+Y robot-left, +Z up. Soles rest near z = -876 in the athletic stance.

Chain offsets (parent-local joint origins, mm):
  pelvis:   waist yaw (0,0,0); hip yaw (0,+-90,-120)
  bracket:  hip roll (0,0,-64)
  carrier:  hip pitch (0,0,-78)
  thigh:    knee (0,0,-290)
  shin:     ankle pitch (0,0,-290)
  ankle:    ankle roll (0,0,-30)
  foot:     sole 26 below origin
  torso:    shoulder pitch (0,+-148,290); neck yaw (0,0,324)
  pod:      shoulder roll (0, s*34, -72)
  housing:  shoulder yaw (0,0,-24)
  bicep:    elbow (0,0,-156)
  forearm:  wrist roll (0,0,-150)
  wrist:    wrist pitch (0,0,-28)
  collar:   neck pitch (0,0,46)
"""

from __future__ import annotations

from build123d import Compound

from cadpy.assembly import AssemblyHelper

from juno_parts.arms import build_bicep, build_forearm
from juno_parts.hand import build_hand
from juno_parts.head import build_head
from juno_parts.joints import (
    build_ankle_link,
    build_hip_bracket,
    build_hip_carrier,
    build_neck_collar,
    build_shoulder_pod,
    build_wrist_carrier,
    build_yaw_housing,
)
from juno_parts.juno_lib import revolute_attach
from juno_parts.legs import build_foot, build_shin, build_thigh
from juno_parts.pelvis import build_pelvis
from juno_parts.torso import build_torso

# ----------------------------------------------------------- pose (degrees)
# Athletic ready stance: knees bent, feet flat, arms relaxed forward.
HIP_PITCH_DEG = -16.0
KNEE_DEG = 32.0
ANKLE_PITCH_DEG = -16.0
HIP_ROLL_ABDUCT_DEG = 3.0       # left +, right -; ankle roll compensates
HIP_YAW_DEG = 0.0
WAIST_YAW_DEG = 0.0
SHOULDER_PITCH_DEG = -8.0      # negative swings the arm forward
SHOULDER_ROLL_ABDUCT_DEG = 8.0  # elbows out
SHOULDER_YAW_INTERNAL_DEG = 8.0
ELBOW_DEG = -20.0               # negative flexes the forearm forward
WRIST_ROLL_DEG = 0.0
WRIST_PITCH_DEG = -2.0
NECK_YAW_DEG = 0.0
NECK_PITCH_DEG = -2.0

HIP_Y = 90.0
SHOULDER_Y = 148.0

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
Z = (0.0, 0.0, 1.0)


def _s(side: str) -> float:
    return 1.0 if side == "left" else -1.0


def assemble() -> Compound:
    asm = AssemblyHelper("juno")

    pelvis = asm.add(build_pelvis(), "pelvis")
    torso = asm.add(build_torso(), "torso")
    revolute_attach(
        asm, pelvis, torso, "waist_yaw",
        (0, 0, 0), Z, X, (0, 0, 0), Z, X, WAIST_YAW_DEG,
    )

    collar = asm.add(build_neck_collar(), "neck_collar")
    revolute_attach(
        asm, torso, collar, "neck_yaw",
        (0, 0, 324), Z, X, (0, 0, 0), Z, X, NECK_YAW_DEG,
    )
    head = asm.add(build_head(), "head")
    revolute_attach(
        asm, collar, head, "neck_pitch",
        (0, 0, 46), Y, X, (0, 0, 0), Y, X, NECK_PITCH_DEG,
    )

    for side in ("left", "right"):
        s = _s(side)

        # ---- leg chain (6 DOF)
        bracket = asm.add(build_hip_bracket(side), f"hip_bracket_{side}")
        revolute_attach(
            asm, pelvis, bracket, f"hip_yaw_{side}",
            (0, s * HIP_Y, -120), Z, X, (0, 0, 0), Z, X, HIP_YAW_DEG,
        )
        carrier = asm.add(build_hip_carrier(side), f"hip_carrier_{side}")
        revolute_attach(
            asm, bracket, carrier, f"hip_roll_{side}",
            (0, 0, -64), X, Y, (0, 0, 0), X, Y, s * HIP_ROLL_ABDUCT_DEG,
        )
        thigh = asm.add(build_thigh(side), f"thigh_{side}")
        revolute_attach(
            asm, carrier, thigh, f"hip_pitch_{side}",
            (0, 0, -78), Y, X, (0, 0, 0), Y, X, HIP_PITCH_DEG,
        )
        shin = asm.add(build_shin(side), f"shin_{side}")
        revolute_attach(
            asm, thigh, shin, f"knee_{side}",
            (0, 0, -290), Y, X, (0, 0, 0), Y, X, KNEE_DEG,
        )
        ankle = asm.add(build_ankle_link(side), f"ankle_link_{side}")
        revolute_attach(
            asm, shin, ankle, f"ankle_pitch_{side}",
            (0, 0, -290), Y, X, (0, 0, 0), Y, X, ANKLE_PITCH_DEG,
        )
        foot = asm.add(build_foot(side), f"foot_{side}")
        revolute_attach(
            asm, ankle, foot, f"ankle_roll_{side}",
            (0, 0, -30), X, Y, (0, 0, 0), X, Y, -s * HIP_ROLL_ABDUCT_DEG,
        )

        # ---- arm chain (6 DOF)
        pod = asm.add(build_shoulder_pod(side), f"shoulder_pod_{side}")
        revolute_attach(
            asm, torso, pod, f"shoulder_pitch_{side}",
            (0, s * SHOULDER_Y, 290), Y, X, (0, 0, 0), Y, X, SHOULDER_PITCH_DEG,
        )
        housing = asm.add(build_yaw_housing(side), f"yaw_housing_{side}")
        revolute_attach(
            asm, pod, housing, f"shoulder_roll_{side}",
            (0, s * 34, -72), X, Y, (0, 0, 0), X, Y, s * SHOULDER_ROLL_ABDUCT_DEG,
        )
        bicep = asm.add(build_bicep(side), f"bicep_{side}")
        revolute_attach(
            asm, housing, bicep, f"shoulder_yaw_{side}",
            (0, 0, -24), Z, X, (0, 0, 0), Z, X, -s * SHOULDER_YAW_INTERNAL_DEG,
        )
        forearm = asm.add(build_forearm(side), f"forearm_{side}")
        revolute_attach(
            asm, bicep, forearm, f"elbow_{side}",
            (0, 0, -156), Y, X, (0, 0, 0), Y, X, ELBOW_DEG,
        )
        wrist = asm.add(build_wrist_carrier(side), f"wrist_carrier_{side}")
        revolute_attach(
            asm, forearm, wrist, f"wrist_roll_{side}",
            (0, 0, -150), Z, X, (0, 0, 0), Z, X, WRIST_ROLL_DEG,
        )
        hand = asm.add(build_hand(side), f"hand_{side}")
        revolute_attach(
            asm, wrist, hand, f"wrist_pitch_{side}",
            (0, 0, -28), Y, X, (0, 0, 0), Y, X, WRIST_PITCH_DEG,
        )

    return asm.build()


def gen_step():
    return assemble()
