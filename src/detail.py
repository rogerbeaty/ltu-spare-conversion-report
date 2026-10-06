from __future__ import annotations

from collections import Counter
from html import escape

import pandas as pd


STATISTIC_CHIP_COLORS = {
    "strike": ("#70AD47", "#FFFFFF"),
    "single pin made": ("#C6E0B4", "#17202A"),
    "multi pin made": ("#E2F0D9", "#17202A"),
    "single pin missed": ("#E60000", "#FFFFFF"),
    "multi pin missed": ("#F4CCCC", "#17202A"),
    "split": ("#FFF2CC", "#17202A"),
    "fill": ("#E7E6E6", "#17202A"),
}


def render_statistic_chips(value: object) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    if not text:
        return ""

    chips = []
    for label in (part.strip() for part in text.split(" / ")):
        background, foreground = _statistic_chip_colors(label)
        display_label = abbreviate_statistic_label(label)
        chips.append(
            "<span style='"
            f"display:inline-block;background:{background};color:{foreground};"
            "padding:0.12rem 0.45rem;border-radius:999px;font-weight:600;"
            "line-height:1.25;margin:0.05rem 0.1rem;white-space:nowrap'>"
            f"{escape(display_label)}</span>"
        )
    return "".join(chips)


def abbreviate_statistic_label(label: str) -> str:
    return label.replace("Single Pin", "SP").replace("Multi Pin", "MP")


def _statistic_chip_colors(label: str) -> tuple[str, str]:
    normalized = label.casefold()
    for prefix, colors in STATISTIC_CHIP_COLORS.items():
        if normalized.startswith(prefix):
            return colors
    return "#E7E6E6", "#17202A"


def sort_ball_by_ball_detail(frame: pd.DataFrame, *, include_athlete: bool = False) -> pd.DataFrame:
    if frame.empty:
        return frame

    out = frame.copy()
    out["__Sort Date"] = pd.to_datetime(out.get("__Game Date"), errors="coerce")
    out["__Frame Order"] = pd.to_numeric(out["Frame"], errors="coerce")
    out["Frame"] = out["__Frame Order"].apply(lambda value: "" if pd.isna(value) else int(value))
    sort_columns = ["__Sort Date", "__Source Order", "__Game Order", "__Frame Order"]
    if include_athlete and "Athlete" in out.columns:
        sort_columns.append("Athlete")
    sort_columns = [column for column in sort_columns if column in out.columns]
    out = out.sort_values(sort_columns, ascending=True, na_position="last").reset_index(drop=True)
    hidden_order_columns = ["__Sort Date", "__Frame Order", "__Game Date", "__Source Order", "__Game Order"]
    return out.drop(columns=[column for column in hidden_order_columns if column in out.columns])


def split_team_baker_detail(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty or "Game Type" not in frame.columns:
        return frame.copy(), frame.iloc[0:0].copy()
    is_baker = frame["Game Type"].astype(str).str.contains("baker", case=False, na=False)
    return (
        frame[~is_baker].reset_index(drop=True),
        frame[is_baker].reset_index(drop=True),
    )


def add_statistic_classification(
    detail: pd.DataFrame,
    records: pd.DataFrame | None,
    frame_attempts: pd.DataFrame | None,
    athlete: str,
) -> pd.DataFrame:
    out = detail.copy()
    if out.empty:
        return _insert_statistic_column(out)

    labels: dict[tuple[str, int], list[str]] = {}

    if frame_attempts is not None and not frame_attempts.empty:
        strikes = frame_attempts[
            (frame_attempts["athlete"] == athlete) & frame_attempts["is_strike"].fillna(False)
        ].copy()
        ball_order = {"Ball1": 1, "Ball2": 2, "Ball3": 3}
        strikes["__ball_order"] = strikes["ball"].map(ball_order).fillna(0)
        strikes = strikes.sort_values(["game_id", "frame", "__ball_order"])
        for _, row in strikes.iterrows():
            labels.setdefault(_key(row["game_id"], row["frame"]), []).append("Strike")

    if records is not None and not records.empty:
        spares = records[records["athlete"] == athlete].copy()
        spares = spares.sort_values(["game_id", "frame"])
        for _, row in spares.iterrows():
            labels.setdefault(_key(row["game_id"], row["frame"]), []).append(_spare_label(row))

    statistics = []
    excluded_fill_rows = []
    for index, row in out.iterrows():
        if _is_other_bowler_fill(row, athlete):
            statistics.append("Fill (Excluded)")
            excluded_fill_rows.append(index)
            continue
        frame_labels = labels.get(_key(row["Game ID"], row["Frame"]), [])
        statistics.append(_combine_labels(frame_labels))

    if excluded_fill_rows:
        blank_columns = [column for column in ("Points", "Ball1", "Ball2") if column in out.columns]
        out[blank_columns] = out[blank_columns].astype(object)
        out.loc[excluded_fill_rows, blank_columns] = ""
    insert_at = out.columns.get_loc("Game Type") if "Game Type" in out.columns else len(out.columns)
    out.insert(insert_at, "Statistic", statistics)
    hidden_columns = [column for column in out.columns if str(column).startswith("__Ball")]
    if hidden_columns:
        out = out.drop(columns=hidden_columns)
    return out


def _insert_statistic_column(frame: pd.DataFrame) -> pd.DataFrame:
    if "Statistic" in frame.columns:
        return frame
    insert_at = frame.columns.get_loc("Game Type") if "Game Type" in frame.columns else len(frame.columns)
    frame.insert(insert_at, "Statistic", "")
    return frame


def _spare_label(row: pd.Series) -> str:
    made = bool(row.get("made", False))
    is_single = bool(row.get("is_single", False))
    is_split = bool(row.get("split", False))
    count_attempt = bool(row.get("count_attempt", False))

    if is_split and not count_attempt:
        return "Split (Excluded)"

    pin_type = "Single Pin" if is_single else "Multi Pin"
    result = "Made" if made else "Missed"
    split_suffix = " (Split)" if is_split else ""
    return f"{pin_type} {result}{split_suffix}"


def _combine_labels(labels: list[str]) -> str:
    counts = Counter(labels)
    combined = []
    for label in dict.fromkeys(labels):
        count = counts[label]
        combined.append(f"{label} ({count})" if count > 1 else label)
    return " / ".join(combined)


def _is_other_bowler_fill(row: pd.Series, athlete: str) -> bool:
    ball1_bowler = str(row.get("__Ball1 Bowler", "")).strip()
    ball2_bowler = str(row.get("__Ball2 Bowler", "")).strip()
    ball3_bowler = str(row.get("__Ball3 Bowler", "")).strip()
    if ball3_bowler != athlete or athlete in {ball1_bowler, ball2_bowler}:
        return False

    ball1 = pd.to_numeric(row.get("Ball1"), errors="coerce")
    ball2 = pd.to_numeric(row.get("Ball2"), errors="coerce")
    ball3 = pd.to_numeric(row.get("Ball3"), errors="coerce")
    if pd.isna(ball1) or pd.isna(ball2) or pd.isna(ball3) or ball3 == 10:
        return False

    spare_fill = ball1 < 10 and ball1 + ball2 == 10
    strike_fill = ball1 == 10 and ball2 == 10
    return bool(spare_fill or strike_fill)


def _key(game_id: object, frame: object) -> tuple[str, int]:
    return str(game_id), int(frame)
