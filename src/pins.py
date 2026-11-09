"""Pin-rack reconstruction shared by on-screen and PDF frame detail."""
from __future__ import annotations

import json
import re
from html import escape

import pandas as pd

PIN_POSITIONS = {7: (12, 12), 8: (36, 12), 9: (60, 12), 10: (84, 12),
                 4: (24, 32), 5: (48, 32), 6: (72, 32),
                 2: (36, 52), 3: (60, 52), 1: (48, 72)}
PIN_COLORS = {"first": "#84BD53", "second": "#F7D65B", "left": "#ED858B", "unknown": "#D5D8DC"}
ALL_PINS = set(range(1, 11))


def _count(value: object) -> int | None:
    if str(value).strip().upper() in {"F", "FOUL"}:
        return 0
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number) or number < 0 or number > 10 or number != int(number):
        return None
    return int(number)


def _leave(value: object, count: int | None) -> set[int] | None:
    if count is None:
        return None
    if count == 10:
        return set()
    if count == 0:
        return ALL_PINS.copy()
    text = "" if value is None or pd.isna(value) else str(value).strip()
    if not re.fullmatch(r"\d+(?:\s*[-,]\s*\d+)*", text):
        return None
    pins = {int(part) for part in re.split(r"\s*[-,]\s*", text)}
    return pins if pins <= ALL_PINS and len(pins) == 10 - count else None


def _rack(first: int | None, second: int | None, leave1: object, leave2: object, label: str) -> dict:
    remaining = _leave(leave1, first)
    colors = {str(pin): "unknown" for pin in ALL_PINS}
    left_text = "Unavailable"
    if remaining is not None:
        left_text = "-".join(map(str, sorted(remaining))) or "None"
        for pin in ALL_PINS - remaining:
            colors[str(pin)] = "first"
        final = None
        if first != 10 and second is not None and first is not None and first + second <= 10:
            final = remaining if second == 0 else _leave(leave2, first + second)
            if final is not None and not final <= remaining:
                final = None
        if final is not None:
            for pin in remaining:
                colors[str(pin)] = "left" if pin in final else "second"
    return {"label": label, "left": left_text, "colors": colors}


def build_pin_diagram(ball1: object, ball2: object, ball3: object = None,
                      leave1: object = None, leave2: object = None, leave3: object = None) -> str:
    first, second, third = map(_count, (ball1, ball2, ball3))
    if first is None:
        return ""
    racks = [_rack(first, second if first < 10 else None, leave1, leave2, "")]
    if first == 10 and second is not None:
        racks[0]["label"] = "Ball 1"
        racks.append(_rack(second, third if second < 10 else None, leave2, leave3,
                           "Balls 2-3" if second < 10 and third is not None else "Ball 2"))
        if second == 10 and third is not None:
            racks.append(_rack(third, None, leave3, None, "Ball 3"))
    elif first < 10 and second is not None and first + second == 10 and third is not None:
        racks[0]["label"] = "Balls 1-2"
        racks.append(_rack(third, None, leave3, None, "Ball 3"))
    return json.dumps(racks)


def pin_racks(value: object) -> list[dict]:
    if not isinstance(value, str) or not value:
        return []
    return json.loads(value)


def render_pin_diagram(value: object) -> str:
    racks = pin_racks(value)
    parts = []
    for rack in racks:
        description = f"{rack['label']} Leave after first delivery: {rack['left']}. "
        description += "; ".join(
            f"{state}: " + ", ".join(pin for pin, color in rack["colors"].items() if color == state)
            for state in ("first", "second", "left", "unknown")
        )
        dots = "".join(
            f'<circle cx="{x}" cy="{y}" r="9" fill="{PIN_COLORS[rack["colors"][str(pin)]]}"/>'
            for pin, (x, y) in PIN_POSITIONS.items()
        )
        parts.append(
            '<div style="text-align:center">'
            + (f'<div style="font-size:0.7rem">{escape(rack["label"])}</div>' if rack["label"] else "")
            + f'<svg width="70" height="61" viewBox="0 0 96 84" role="img" aria-label="{escape(description, quote=True)}">{dots}</svg>'
            + f'<div style="font-size:0.7rem">Left: {escape(rack["left"])}</div></div>'
        )
    return '<div style="display:flex;gap:6px;justify-content:center">' + "".join(parts) + "</div>" if parts else ""
