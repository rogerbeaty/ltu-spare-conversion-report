from __future__ import annotations

import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Any

import pandas as pd

FULL_RACK_LEAVE = "1-2-3-4-5-6-7-8-9-10"
PIN_MIRROR_MAP = {1: 1, 2: 3, 3: 2, 4: 6, 5: 5, 6: 4, 7: 10, 8: 9, 9: 8, 10: 7}
FORCED_SPLIT_BASE_LEAVES = {"1-2-10", "1-3-7", "1-2-4-10"}
FORCED_SPLIT_LEAVES = set(FORCED_SPLIT_BASE_LEAVES)
for _leave in list(FORCED_SPLIT_BASE_LEAVES):
    FORCED_SPLIT_LEAVES.add(
        "-".join(str(p) for p in sorted(PIN_MIRROR_MAP[int(part)] for part in _leave.split("-")))
    )

EVENT_NAME_ALIASES = {
    "whac 1": "2026 WHAC 1",
    "whac 1 jax 60": "2026 WHAC 1",
    "2026 whac1": "2026 WHAC 1",
    "2026 whac 1": "2026 WHAC 1",
    "2026 detroit mcc": "2026 Detroit MCC",
    "2026 midwest collegiate championship": "2026 Detroit MCC",
}
ATHLETE_NAME_ALIASES = {
    "matt schepis": "Matthew Schepis",
    "matthew schepis": "Matthew Schepis",
    "serena miller": "Serina Miller",
    "serina miller": "Serina Miller",
    "william cunningham": "William Cunningham III",
    "william cunningham iii": "William Cunningham III",
}


@dataclass
class ParseResult:
    records: pd.DataFrame
    frame_attempts: pd.DataFrame
    baker_frames: pd.DataFrame
    team_games: pd.DataFrame
    detected_format: str
    games_processed: int
    spare_opportunities: int
    athletes_found: int


def parse_bowlsheet_csv(csv_bytes: bytes) -> ParseResult:
    csv_bytes = preprocess_bowlsheet_export(csv_bytes)
    detected = detect_format(csv_bytes)
    if detected == "Frame-columns":
        records, frame_attempts, baker_frames, team_games, games_processed = _parse_format_a(csv_bytes)
    else:
        records, frame_attempts, baker_frames, team_games, games_processed = _parse_format_b(csv_bytes)

    athlete_names: set[str] = set()
    for frame in (records, frame_attempts, baker_frames, team_games):
        if not frame.empty:
            frame["event"] = frame["event"].map(canonicalize_event_name)
            frame["athlete"] = frame["athlete"].map(canonicalize_athlete_name)
            athlete_names.update(frame["athlete"].dropna().astype(str))

    return ParseResult(
        records=records,
        frame_attempts=frame_attempts,
        baker_frames=baker_frames,
        team_games=team_games,
        detected_format=detected,
        games_processed=games_processed,
        spare_opportunities=int(len(records)),
        athletes_found=len(athlete_names),
    )


def canonicalize_event_name(value: object) -> object:
    if value is None or pd.isna(value):
        return value
    text = str(value).strip()
    return EVENT_NAME_ALIASES.get(text.casefold(), text)


def canonicalize_athlete_name(value: object) -> object:
    if value is None or pd.isna(value):
        return value
    text = str(value).strip()
    return ATHLETE_NAME_ALIASES.get(text.casefold(), text)


def detect_format(csv_bytes: bytes) -> str:
    header_df = _read_csv_with_fallback(csv_bytes, nrows=0)
    cols = [str(c) for c in header_df.columns]
    if any("Frame1Ball1" in c for c in cols):
        return "Frame-columns"
    return "Delivery-grid"


def extract_team_names(csv_bytes: bytes) -> list[str]:
    csv_bytes = preprocess_bowlsheet_export(csv_bytes)
    if detect_format(csv_bytes) == "Frame-columns":
        df = _read_csv_with_fallback(csv_bytes)
        team_column = next(
            (column for column in df.columns if _normalize_colname(column) == "team"),
            None,
        )
        if team_column is None:
            return []
        values = df[team_column]
    else:
        df = _read_csv_with_fallback(csv_bytes, header=None)
        if len(df.columns) <= 6:
            return []
        values = df.iloc[:, 6]

    return list(
        dict.fromkeys(
            text
            for value in values
            if (text := _clean_text(value)) and text.casefold() != "team"
        )
    )


def _parse_format_a(csv_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, int]:
    df = _read_csv_with_fallback(csv_bytes)
    rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    baker_rows: list[dict[str, Any]] = []
    team_game_rows: list[dict[str, Any]] = []
    col_map = {_normalize_colname(c): c for c in df.columns}
    metadata_cols = [c for c in df.columns if "Frame" not in str(c)]

    for game_idx, row in df.iterrows():
        game_id = int(game_idx)
        event = _extract_event_format_a(row, col_map, metadata_cols)
        game_date = _extract_import_date(row)
        game_type = _extract_game_type_format_a(row, col_map, metadata_cols)
        game_bowlers = [
            _clean_text(row.get(f"Frame{frame}Ball{ball}Bowler"))
            for frame in range(1, 11)
            for ball in range(1, 4 if frame == 10 else 3)
        ]
        team_game = _build_team_game(
            game_id,
            event,
            game_date,
            game_type,
            game_bowlers,
            row.get(col_map.get("frame10score")) if col_map.get("frame10score") is not None else None,
        )
        if team_game:
            team_game_rows.append(team_game)
        for frame in range(1, 11):
            b1 = _to_knocked(row.get(f"Frame{frame}Ball1"))
            b2 = _to_knocked(row.get(f"Frame{frame}Ball2"))
            b3 = _to_knocked(row.get("Frame10Ball3")) if frame == 10 else None

            b1_bowler = _clean_text(row.get(f"Frame{frame}Ball1Bowler"))
            b2_bowler = _clean_text(row.get(f"Frame{frame}Ball2Bowler"))
            b3_bowler = _clean_text(row.get("Frame10Ball3Bowler")) if frame == 10 else ""
            frame_bowler = b1_bowler or b2_bowler
            frame_attempt = _build_frame_attempt(game_id, event, game_type, frame, frame_bowler, b1)
            if frame_attempt:
                frame_attempt["game_date"] = game_date
                frame_rows.append(frame_attempt)
            baker_frame = _build_baker_frame(game_id, event, game_date, game_type, frame, frame_bowler, b1, b2)
            if baker_frame:
                baker_rows.append(baker_frame)

            if frame < 10:
                rec = _build_normal_opportunity(
                    game_id=game_id,
                    event=event,
                    game_date=game_date,
                    frame=frame,
                    bowler=frame_bowler,
                    ball1=b1,
                    ball2=b2,
                    split=row.get(f"Frame{frame}Ball1Split"),
                    leave=row.get(f"Frame{frame}Ball1Pins"),
                )
                if rec:
                    rows.append(rec)
                continue

            # Frame 10 normal path when ball1 is not strike.
            rec = _build_normal_opportunity(
                game_id=game_id,
                event=event,
                game_date=game_date,
                frame=frame,
                bowler=frame_bowler,
                ball1=b1,
                ball2=b2,
                split=row.get("Frame10Ball1Split"),
                leave=row.get("Frame10Ball1Pins"),
            )
            if rec:
                rows.append(rec)

            # Frame 10 special strike->spare sequence.
            special = _build_tenth_special(
                game_id=game_id,
                event=event,
                game_date=game_date,
                frame=10,
                ball1=b1,
                ball2=b2,
                ball3=b3,
                bowler=b3_bowler,
                split=row.get("Frame10Ball2Split"),
                leave=row.get("Frame10Ball2Pins"),
            )
            if special:
                rows.append(special)

            baker_rows.extend(
                _build_baker_tenth_bonus_rows(
                    game_id=game_id,
                    event=event,
                    game_date=game_date,
                    game_type=game_type,
                    ball1=b1,
                    ball2=b2,
                    ball3=b3,
                    ball2_bowler=b2_bowler,
                    ball3_bowler=b3_bowler,
                )
            )
            frame_rows.extend(
                _build_tenth_strike_attempt_rows(
                    game_id=game_id,
                    event=event,
                    game_date=game_date,
                    game_type=game_type,
                    ball1=b1,
                    ball2=b2,
                    ball3=b3,
                    ball2_bowler=b2_bowler,
                    ball3_bowler=b3_bowler,
                )
            )

    return (
        _finalize_records(rows),
        _finalize_frame_attempts(frame_rows),
        _finalize_baker_frames(baker_rows),
        _finalize_team_games(team_game_rows),
        int(len(df)),
    )


def _parse_format_b(csv_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, int]:
    df = _read_csv_with_fallback(csv_bytes, header=None)
    rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    baker_rows: list[dict[str, Any]] = []
    team_game_rows: list[dict[str, Any]] = []

    for game_idx, row in df.iterrows():
        game_id = int(game_idx)
        event = _extract_event_format_b(row)
        game_date = _extract_import_date(row)
        game_type = _extract_game_type_format_b(row)
        bowlers = [_clean_text(row.get(i)) for i in range(7, 28)]
        splits = [row.get(i) for i in range(38, 59)]
        knocked = [_to_knocked(row.get(i)) for i in range(59, 80)]
        leaves = [row.get(i) for i in range(80, 101)]
        team_game = _build_team_game(game_id, event, game_date, game_type, bowlers, row.get(37))
        if team_game:
            team_game_rows.append(team_game)

        # Frames 1-9: delivery pairs (0,1) ... (16,17)
        for frame in range(1, 10):
            i = (frame - 1) * 2
            frame_bowler = bowlers[i] or bowlers[i + 1]
            frame_attempt = _build_frame_attempt(game_id, event, game_type, frame, frame_bowler, knocked[i])
            if frame_attempt:
                frame_attempt["game_date"] = game_date
                frame_rows.append(frame_attempt)
            baker_frame = _build_baker_frame(game_id, event, game_date, game_type, frame, frame_bowler, knocked[i], knocked[i + 1])
            if baker_frame:
                baker_rows.append(baker_frame)
            rec = _build_normal_opportunity(
                game_id=game_id,
                event=event,
                game_date=game_date,
                frame=frame,
                bowler=frame_bowler,
                ball1=knocked[i],
                ball2=knocked[i + 1],
                split=splits[i],
                leave=leaves[i],
            )
            if rec:
                rows.append(rec)

        # Frame 10 normal path on deliveries 18,19.
        frame10_bowler = bowlers[18] or bowlers[19]
        frame10_attempt = _build_frame_attempt(game_id, event, game_type, 10, frame10_bowler, knocked[18])
        if frame10_attempt:
            frame10_attempt["game_date"] = game_date
            frame_rows.append(frame10_attempt)
        baker_frame10 = _build_baker_frame(game_id, event, game_date, game_type, 10, frame10_bowler, knocked[18], knocked[19])
        if baker_frame10:
            baker_rows.append(baker_frame10)
        rec = _build_normal_opportunity(
            game_id=game_id,
            event=event,
            game_date=game_date,
            frame=10,
            bowler=frame10_bowler,
            ball1=knocked[18],
            ball2=knocked[19],
            split=splits[18],
            leave=leaves[18],
        )
        if rec:
            rows.append(rec)

        # Frame 10 special strike->spare sequence on deliveries 19,20.
        special = _build_tenth_special(
            game_id=game_id,
            event=event,
            game_date=game_date,
            frame=10,
            ball1=knocked[18],
            ball2=knocked[19],
            ball3=knocked[20],
            bowler=bowlers[20],
            split=splits[19],
            leave=leaves[19],
        )
        if special:
            rows.append(special)

        baker_rows.extend(
            _build_baker_tenth_bonus_rows(
                game_id=game_id,
                event=event,
                game_date=game_date,
                game_type=game_type,
                ball1=knocked[18],
                ball2=knocked[19],
                ball3=knocked[20],
                ball2_bowler=bowlers[19],
                ball3_bowler=bowlers[20],
            )
        )
        frame_rows.extend(
            _build_tenth_strike_attempt_rows(
                game_id=game_id,
                event=event,
                game_date=game_date,
                game_type=game_type,
                ball1=knocked[18],
                ball2=knocked[19],
                ball3=knocked[20],
                ball2_bowler=bowlers[19],
                ball3_bowler=bowlers[20],
            )
        )

    return (
        _finalize_records(rows),
        _finalize_frame_attempts(frame_rows),
        _finalize_baker_frames(baker_rows),
        _finalize_team_games(team_game_rows),
        int(len(df)),
    )


def _build_normal_opportunity(
    game_id: int,
    event: str,
    game_date: str,
    frame: int,
    bowler: str,
    ball1: int | None,
    ball2: int | None,
    split: Any,
    leave: Any,
) -> dict[str, Any] | None:
    if not bowler or ball1 is None or ball1 >= 10:
        return None

    leave_str = _normalize_leave(leave)
    if leave_str == FULL_RACK_LEAVE:
        return None

    made = ball2 is not None and (ball1 + ball2 == 10)
    is_single = ball1 == 9
    is_split = _to_bool(split) or _is_forced_split_leave(leave_str)

    count_attempt = True
    if not is_single and is_split and not made:
        count_attempt = False

    return {
        "game_id": game_id,
        "event": event,
        "game_date": game_date,
        "frame": frame,
        "athlete": bowler,
        "pin_leave": leave_str,
        "is_single": is_single,
        "split": is_split,
        "made": bool(made),
        "count_attempt": count_attempt,
    }


def _build_tenth_special(
    game_id: int,
    event: str,
    game_date: str,
    frame: int,
    ball1: int | None,
    ball2: int | None,
    ball3: int | None,
    bowler: str,
    split: Any,
    leave: Any,
) -> dict[str, Any] | None:
    if ball1 != 10 or ball2 is None or ball2 >= 10 or ball3 is None or not bowler:
        return None

    leave_str = _normalize_leave(leave)
    if leave_str == FULL_RACK_LEAVE:
        return None

    made = (ball2 + ball3) == 10
    # A missed Ball3 conversion is a fill shot, not a spare attempt.
    if not made:
        return None
    is_single = ball2 == 9
    is_split = _to_bool(split) or _is_forced_split_leave(leave_str)

    return {
        "game_id": game_id,
        "event": event,
        "game_date": game_date,
        "frame": frame,
        "athlete": bowler,
        "pin_leave": leave_str,
        "is_single": is_single,
        "split": is_split,
        "made": bool(made),
        "count_attempt": True,
    }


def _finalize_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "game_id",
                "event",
                "game_date",
                "frame",
                "athlete",
                "pin_leave",
                "is_single",
                "split",
                "made",
                "count_attempt",
            ]
        )

    records = pd.DataFrame(rows)
    records["pin_leave"] = records["pin_leave"].fillna("").astype(str)
    return records


def _finalize_frame_attempts(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["game_id", "event", "game_date", "game_type", "frame", "ball", "pins", "athlete", "is_strike"]
        )
    return pd.DataFrame(rows)


def _build_frame_attempt(
    game_id: int,
    event: str,
    game_type: str,
    frame: int,
    bowler: str,
    ball1: int | None,
) -> dict[str, Any] | None:
    if not bowler or ball1 is None:
        return None
    return {
        "game_id": game_id,
        "event": event,
        "game_date": "",
        "game_type": game_type,
        "frame": frame,
        "ball": "Ball1",
        "pins": int(ball1),
        "athlete": bowler,
        "is_strike": bool(ball1 == 10),
    }


def _finalize_baker_frames(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["game_id", "event", "game_date", "game_type", "frame", "athlete", "points"])
    return pd.DataFrame(rows)


def _finalize_team_games(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["game_id", "event", "game_date", "game_type", "athlete", "score"])
    return pd.DataFrame(rows)


def _build_team_game(
    game_id: int,
    event: str,
    game_date: str,
    game_type: str,
    bowlers: list[str],
    frame10_score: Any,
) -> dict[str, Any] | None:
    if _clean_text(game_type).lower() != "team":
        return None
    score = _to_knocked(frame10_score)
    starting_bowler = next((bowler for bowler in bowlers if bowler), "")
    if score is None or not starting_bowler:
        return None
    return {
        "game_id": game_id,
        "event": event,
        "game_date": game_date,
        "game_type": game_type,
        "athlete": starting_bowler,
        "score": score,
    }


def _build_baker_frame(
    game_id: int,
    event: str,
    game_date: str,
    game_type: str,
    frame: int,
    bowler: str,
    ball1: int | None,
    ball2: int | None,
) -> dict[str, Any] | None:
    if not _is_baker_game(game_type) or not bowler or ball1 is None:
        return None

    if ball1 == 10:
        points = 20
    elif ball2 is not None and (ball1 + ball2 == 10):
        points = 10 + ball1
    else:
        points = ball1 + (ball2 or 0)

    return {
        "game_id": game_id,
        "event": event,
        "game_date": game_date,
        "game_type": game_type,
        "frame": frame,
        "athlete": bowler,
        "points": int(points),
    }


def _build_baker_tenth_bonus_rows(
    game_id: int,
    event: str,
    game_date: str,
    game_type: str,
    ball1: int | None,
    ball2: int | None,
    ball3: int | None,
    ball2_bowler: str,
    ball3_bowler: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not _is_baker_game(game_type) or ball1 is None or ball2 is None or ball3 is None:
        return rows

    first_is_strike = ball1 == 10
    first_is_spare = (ball1 < 10) and (ball1 + ball2 == 10)

    # Ignore Ball3 from a different bowler unless Ball3 is a strike.
    ball3_same_bowler = ball3_bowler and (ball3_bowler.strip().lower() == ball2_bowler.strip().lower())
    effective_ball3_for_ball2 = ball3 if (ball3 == 10 or ball3_same_bowler) else None

    # After a strike, count Ball2 as another Baker frame only when it is a
    # strike or Ball3 completes its spare. A missed Ball3 is an excluded fill.
    if first_is_strike and ball2_bowler:
        if ball2 == 10:
            second_points = 20
        elif effective_ball3_for_ball2 is not None and (ball2 + effective_ball3_for_ball2 == 10):
            second_points = 10 + ball2
        else:
            second_points = None
        if second_points is not None:
            rows.append(
                {
                    "game_id": game_id,
                    "event": event,
                    "game_date": game_date,
                    "game_type": game_type,
                    "frame": 10,
                    "athlete": ball2_bowler,
                    "points": int(second_points),
                }
            )

    # Third-ball strike counts as an additional attempt in:
    # - strike, strike, strike
    # - spare, strike
    third_is_extra = (ball3 == 10) and ((first_is_strike and ball2 == 10) or first_is_spare)
    if third_is_extra and ball3_bowler:
        rows.append(
            {
                "game_id": game_id,
                "event": event,
                "game_date": game_date,
                "game_type": game_type,
                "frame": 10,
                "athlete": ball3_bowler,
                "points": 20,
            }
        )
    return rows


def _is_baker_game(game_type: str) -> bool:
    return "baker" in _clean_text(game_type).lower()


def _build_tenth_strike_attempt_rows(
    game_id: int,
    event: str,
    game_date: str,
    game_type: str,
    ball1: int | None,
    ball2: int | None,
    ball3: int | None,
    ball2_bowler: str,
    ball3_bowler: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # If the first ball in the 10th is a strike, ball2 is also a strike attempt.
    if ball1 == 10 and ball2 is not None and ball2_bowler:
        rows.append(
            {
                "game_id": game_id,
                "event": event,
                "game_date": game_date,
                "game_type": game_type,
                "frame": 10,
                "ball": "Ball2",
                "pins": int(ball2),
                "athlete": ball2_bowler,
                "is_strike": bool(ball2 == 10),
            }
        )

    # Ball3 is a strike attempt only after two strikes or after a Ball1/Ball2 spare.
    # After strike/non-strike, Ball3 belongs exclusively to the spare attempt.
    first_two_are_strikes = ball1 == 10 and ball2 == 10
    first_two_are_spare = (
        ball1 is not None
        and ball2 is not None
        and ball1 < 10
        and (ball1 + ball2 == 10)
    )
    if ball3 == 10 and ball3_bowler and (first_two_are_strikes or first_two_are_spare):
        rows.append(
            {
                "game_id": game_id,
                "event": event,
                "game_date": game_date,
                "game_type": game_type,
                "frame": 10,
                "ball": "Ball3",
                "pins": 10,
                "athlete": ball3_bowler,
                "is_strike": True,
            }
        )

    return rows


def _to_knocked(value: Any) -> int | None:
    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        return int(value)

    txt = str(value).strip().upper()
    if not txt:
        return None
    if txt in {"F", "FOUL"}:
        return 0
    if txt in {"X", "STRIKE"}:
        return 10

    try:
        return int(float(txt))
    except ValueError:
        return None


def _to_bool(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1

    txt = str(value).strip().lower()
    return txt in {"1", "true", "yes", "y"}


def _clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_leave(value: Any) -> str:
    txt = _clean_text(value).replace(" ", "")
    return txt


def _is_forced_split_leave(leave_str: str) -> bool:
    return leave_str in FORCED_SPLIT_LEAVES


def _extract_event_format_a(row: pd.Series, col_map: dict[str, Any], metadata_cols: list[Any]) -> str:
    preferred = [
        "event",
        "eventname",
        "tournament",
        "tournamentname",
        "session",
        "series",
        "league",
        "competition",
        "competitionname",
        "meet",
    ]
    for key in preferred:
        col = col_map.get(key)
        if col is not None:
            value = _clean_text(row.get(col))
            if value:
                return value

    for col in metadata_cols:
        value = _clean_text(row.get(col))
        if value:
            return value

    return "Unknown Event"


def _extract_event_format_b(row: pd.Series) -> str:
    for i in range(0, 7):
        value = _clean_text(row.get(i))
        if value:
            return value
    return "Unknown Event"


def _extract_import_date(row: pd.Series) -> str:
    # BowlSheet import date is expected in the 4th column (index 3).
    if len(row) <= 3:
        return ""
    return _clean_text(row.iloc[3])


def _extract_game_type_format_a(row: pd.Series, col_map: dict[str, Any], metadata_cols: list[Any]) -> str:
    col = col_map.get("gametype")
    if col is not None:
        value = _clean_text(row.get(col))
        if value:
            return value

    for col_name in metadata_cols:
        value = _clean_text(row.get(col_name))
        if not value:
            continue
        lower = value.lower()
        if "baker" in lower or "team" in lower:
            return value
    return "Unknown"


def _extract_game_type_format_b(row: pd.Series) -> str:
    for i in range(0, 7):
        value = _clean_text(row.get(i))
        if not value:
            continue
        lower = value.lower()
        if "baker" in lower or "team" in lower:
            return value
    return "Unknown"


def _normalize_colname(name: Any) -> str:
    raw = str(name).lower()
    return "".join(ch for ch in raw if ch.isalnum())


def _read_csv_with_fallback(csv_bytes: bytes, **kwargs: Any) -> pd.DataFrame:
    encodings = ("utf-8-sig", "utf-8", "cp1252", "latin1")
    last_exc: Exception | None = None

    for enc in encodings:
        try:
            return pd.read_csv(BytesIO(csv_bytes), encoding=enc, **kwargs)
        except UnicodeDecodeError as exc:
            last_exc = exc

    # Final defensive fallback so malformed bytes don't crash parsing.
    return pd.read_csv(BytesIO(csv_bytes), encoding="utf-8", encoding_errors="replace", **kwargs)


def preprocess_bowlsheet_export(csv_bytes: bytes) -> bytes:
    text = _decode_csv_bytes(csv_bytes)
    rows = list(csv.reader(StringIO(text)))
    if not rows:
        return csv_bytes

    first_row_empty = all(_clean_text(value) == "" for value in rows[0])
    if not first_row_empty:
        return csv_bytes

    cleaned = [row for index, row in enumerate(rows) if index not in (0, 2)]
    if not cleaned:
        return b""

    # BowlSheet can emit a stray trailing field in its disposable label row.
    # Pad retained rows to a consistent width before handing them to pandas.
    width = max(len(row) for row in cleaned)
    normalized = [row + ([""] * (width - len(row))) for row in cleaned]
    output = StringIO(newline="")
    csv.writer(output).writerows(normalized)
    return output.getvalue().encode("utf-8")


def _decode_csv_bytes(csv_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return csv_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return csv_bytes.decode("utf-8", errors="replace")
