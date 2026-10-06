from __future__ import annotations


MEN_SINGLE_PIN_THRESHOLDS = (0.90, 0.80)
WOMEN_SINGLE_PIN_THRESHOLDS = (0.85, 0.75)
MULTI_PIN_THRESHOLDS = (0.80, 0.50)


def single_pin_thresholds(team: str) -> tuple[float, float]:
    if str(team).strip().casefold() == "women":
        return WOMEN_SINGLE_PIN_THRESHOLDS
    return MEN_SINGLE_PIN_THRESHOLDS
