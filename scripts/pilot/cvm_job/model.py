"""CVM pilot model selector and resolved-model contract."""

from __future__ import annotations


DEFAULT_MODEL_SELECTOR = "gpt-5.5"
LEGACY_DEFAULT_MODEL = "gpt-5.6-sol"

# The shell pilot passes a selector to ``gateway/codex-tap-gpt56``.  The job
# receipt records the concrete model name that selector resolves to.
MODEL_RESOLUTION = {
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
    "gpt-5.5": "gpt-5.5",
}
MODEL_SELECTORS = tuple(MODEL_RESOLUTION)
_SELECTOR_BY_MODEL = {
    resolved: selector for selector, resolved in MODEL_RESOLUTION.items()
}


def resolve_model(selector: str | None = None) -> tuple[str, str]:
    """Return ``(selector, concrete_model)`` for one accepted pilot choice."""

    selected = selector or DEFAULT_MODEL_SELECTOR
    try:
        return selected, MODEL_RESOLUTION[selected]
    except (KeyError, TypeError) as error:
        choices = "|".join(MODEL_SELECTORS)
        raise ValueError(
            f"invalid model selector: {selected!r}; expected {choices}"
        ) from error


def selector_for_model(model: str) -> str:
    """Return the gateway selector for a resolved model in a job receipt."""

    try:
        return _SELECTOR_BY_MODEL[model]
    except (KeyError, TypeError) as error:
        choices = "|".join(_SELECTOR_BY_MODEL)
        raise ValueError(
            f"invalid resolved model: {model!r}; expected {choices}"
        ) from error
