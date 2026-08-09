"""Load and identify the frozen CADENA residual profile."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
from typing import Any


PROFILE_NAME = "cadena_residual_eight_view/1"
_PROFILE_RESOURCE = "cadena_residual_eight_view_v1.json"
_VIEW_NAMES = ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")


@dataclass(frozen=True)
class LoadedProfile:
    """Parsed profile plus the identity of its exact versioned bytes."""

    profile: dict[str, Any]
    sha256: str


def load_profile() -> LoadedProfile:
    """Return the one supported residual profile and its SHA-256 identity."""

    raw = (
        files("meshshot")
        .joinpath("profiles")
        .joinpath(_PROFILE_RESOURCE)
        .read_bytes()
    )
    profile = json.loads(raw)
    _validate_profile(profile)
    return LoadedProfile(profile=profile, sha256=hashlib.sha256(raw).hexdigest())


def _validate_profile(profile: object) -> None:
    if not isinstance(profile, dict):
        raise ValueError("meshshot profile must be a JSON object")
    if profile.get("schema") != "meshshot.camera-profile/1":
        raise ValueError("unsupported meshshot profile schema")
    if profile.get("name") != PROFILE_NAME:
        raise ValueError("unsupported meshshot profile name")
    views = profile.get("views")
    if not isinstance(views, list) or tuple(
        view.get("name") for view in views if isinstance(view, dict)
    ) != _VIEW_NAMES:
        raise ValueError("meshshot profile view order is invalid")
    variants = profile.get("variants")
    if not isinstance(variants, dict) or set(variants) != {"step", "final"}:
        raise ValueError("meshshot profile variants are invalid")
