from __future__ import annotations

import csv
from io import BytesIO
from io import StringIO

import pandas as pd

from src.parser import (
    canonicalize_athlete_name,
    canonicalize_event_name,
    detect_format,
    parse_bowlsheet_csv,
)


def test_canonical_event_and_athlete_names() -> None:
    assert canonicalize_event_name("WHAC 1 JAX 60") == "2026 WHAC 1"
    assert canonicalize_event_name("WHAC 1") == "2026 WHAC 1"
    assert canonicalize_event_name("2026 Midwest Collegiate Championship") == "2026 Detroit MCC"
    assert canonicalize_athlete_name("Matt Schepis") == "Matthew Schepis"
    assert canonicalize_athlete_name("Serena Miller") == "Serina Miller"
    assert canonicalize_athlete_name("William Cunningham") == "William Cunningham III"


def test_parse_format_a_counts_and_split_rules() -> None:
    df = pd.DataFrame(
        [
            {
                "Frame1Ball1Bowler": "Alice",
                "Frame1Ball1": 9,
                "Frame1Ball2": 1,
                "Frame1Ball1Pins": "10",
                "Frame1Ball1Split": 0,
                "Frame2Ball1Bowler": "Alice",
                "Frame2Ball1": 8,
                "Frame2Ball2": 2,
                "Frame2Ball1Pins": "3-10",
                "Frame2Ball1Split": 0,
                "Frame3Ball1Bowler": "Alice",
                "Frame3Ball1": 8,
                "Frame3Ball2": 1,
                "Frame3Ball1Pins": "4-7-10",
                "Frame3Ball1Split": 1,
                "Frame4Ball1Bowler": "Alice",
                "Frame4Ball1": 8,
                "Frame4Ball2": 2,
                "Frame4Ball1Pins": "2-4-5-8",
                "Frame4Ball1Split": 1,
                "Frame10Ball1Bowler": "Alice",
                "Frame10Ball1": 10,
                "Frame10Ball2": 7,
                "Frame10Ball3": 3,
                "Frame10Ball2Pins": "2-4-8",
                "Frame10Ball2Split": 0,
                "Frame10Ball3Bowler": "Alice",
            }
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    assert detect_format(csv_bytes) == "Frame-columns"

    result = parse_bowlsheet_csv(csv_bytes)
    records = result.records
    frame_attempts = result.frame_attempts

    assert result.detected_format == "Frame-columns"
    assert result.games_processed == 1
    assert result.spare_opportunities == 5
    assert result.athletes_found == 1

    assert int(records["count_attempt"].sum()) == 4
    assert int(len(frame_attempts)) >= 4
    assert int(frame_attempts["is_strike"].sum()) >= 1

    multi = records[~records["is_single"]]
    assert int(multi["count_attempt"].sum()) == 3
    assert int((multi["made"] & multi["count_attempt"]).sum()) == 3


def test_team_game_scores_use_starter_and_exclude_baker_games() -> None:
    df = pd.DataFrame(
        [
            {
                "Event": "Event 1",
                "GameType": "Team",
                "Frame1Ball1Bowler": "Alex",
                "Frame7Ball1Bowler": "Blake",
                "Frame10Score": 200,
            },
            {
                "Event": "Event 1",
                "GameType": "Team",
                "Frame1Ball1Bowler": "Alex",
                "Frame10Ball1Bowler": "Alex",
                "Frame10Score": 180,
            },
            {
                "Event": "Event 1",
                "GameType": "Baker",
                "Frame1Ball1Bowler": "Alex",
                "Frame10Score": 250,
            },
        ]
    )

    result = parse_bowlsheet_csv(df.to_csv(index=False).encode())

    assert result.team_games["athlete"].tolist() == ["Alex", "Alex"]
    assert result.team_games["score"].tolist() == [200, 180]


def test_parse_format_b_counts_and_full_rack_exclusion() -> None:
    row = [""] * 101

    for i in range(7):
        row[i] = f"meta{i}"

    for i in range(21):
        row[7 + i] = "Bob"

    # Default all splits off.
    for i in range(21):
        row[38 + i] = 0

    # Default all knocked to strikes.
    for i in range(21):
        row[59 + i] = 10

    # Default leaves.
    for i in range(21):
        row[80 + i] = ""

    # Frame 1: single pin made (9 + 1)
    row[59 + 0] = 9
    row[59 + 1] = 1
    row[80 + 0] = "10"

    # Frame 2: split miss (8 + 1), excluded from multi attempts
    row[59 + 2] = 8
    row[59 + 3] = 1
    row[38 + 2] = 1
    row[80 + 2] = "4-6-7"

    # Frame 3: full rack leave should be excluded entirely
    row[59 + 4] = 8
    row[59 + 5] = 2
    row[80 + 4] = "1-2-3-4-5-6-7-8-9-10"

    # Frame 10 special: strike then 6 + 4, made
    row[59 + 18] = 10
    row[59 + 19] = 6
    row[59 + 20] = 4
    row[80 + 19] = "2-4-8"

    df = pd.DataFrame([row])
    buff = BytesIO()
    df.to_csv(buff, index=False, header=False)
    csv_bytes = buff.getvalue()

    assert detect_format(csv_bytes) == "Delivery-grid"

    result = parse_bowlsheet_csv(csv_bytes)
    records = result.records
    frame_attempts = result.frame_attempts

    assert result.detected_format == "Delivery-grid"
    assert result.games_processed == 1
    assert result.spare_opportunities == 3
    assert result.athletes_found == 1

    single = records[records["is_single"]]
    multi = records[~records["is_single"]]

    assert int(single["count_attempt"].sum()) == 1
    assert int((single["made"] & single["count_attempt"]).sum()) == 1
    assert int(multi["count_attempt"].sum()) == 1
    assert int((multi["made"] & multi["count_attempt"]).sum()) == 1
    assert int(frame_attempts["is_strike"].sum()) >= 1


def test_parse_format_b_drops_first_and_third_rows_when_first_row_empty() -> None:
    valid = [""] * 101
    for i in range(21):
        valid[7 + i] = "Sam"
        valid[38 + i] = 0
        valid[59 + i] = 10
        valid[80 + i] = ""
    valid[59 + 0] = 9
    valid[59 + 1] = 1
    valid[80 + 0] = "10"

    empty_top = [""] * 101
    third_noise = ["junk"] * 101

    df = pd.DataFrame([empty_top, valid, third_noise])
    buff = BytesIO()
    df.to_csv(buff, index=False, header=False)
    csv_bytes = buff.getvalue()

    result = parse_bowlsheet_csv(csv_bytes)
    assert result.detected_format == "Delivery-grid"
    assert result.games_processed == 1
    assert result.spare_opportunities == 1
    assert result.athletes_found == 1


def test_preprocessing_accepts_uneven_disposable_rows() -> None:
    valid = [""] * 101
    for i in range(21):
        valid[7 + i] = "Sam"
        valid[38 + i] = 0
        valid[59 + i] = 10
        valid[80 + i] = ""
    valid[59] = 9
    valid[60] = 1
    valid[80] = "10"

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([""] * 101)
    writer.writerow(valid)
    writer.writerow(["junk"] * 102)

    result = parse_bowlsheet_csv(output.getvalue().encode("utf-8"))

    assert result.detected_format == "Delivery-grid"
    assert result.games_processed == 1
    assert result.spare_opportunities == 1
    assert result.athletes_found == 1


def test_baker_scoring_and_tenth_fill_rule() -> None:
    df = pd.DataFrame(
        [
            {
                # strike then spare in 10th => two counted attempts (20 + 17)
                "GameType": "Baker",
                "Frame1Ball1Bowler": "Alex",
                "Frame1Ball1": 10,
                "Frame2Ball1Bowler": "Alex",
                "Frame2Ball1": 9,
                "Frame2Ball2": 1,
                "Frame3Ball1Bowler": "Alex",
                "Frame3Ball1": 6,
                "Frame3Ball2": 3,
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball1": 10,
                "Frame10Ball2": 7,
                "Frame10Ball3": 3,
                "Frame10Ball3Bowler": "Blake",
            },
            {
                # spare then strike in 10th => two counted attempts (19 + 20)
                "GameType": "Baker",
                "Frame1Ball1Bowler": "Alex",
                "Frame1Ball1": 10,
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball1": 9,
                "Frame10Ball2": 1,
                "Frame10Ball3": 10,
                "Frame10Ball3Bowler": "Blake",
            },
            {
                # XXX in 10th => three counted attempts (20 + 20 + 20)
                "GameType": "Baker",
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball1": 10,
                "Frame10Ball2": 10,
                "Frame10Ball3": 10,
                "Frame10Ball3Bowler": "Blake",
            },
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    result = parse_bowlsheet_csv(csv_bytes)
    baker = result.baker_frames
    strike_attempts = result.frame_attempts

    alex_points = int(baker[baker["athlete"] == "Alex"]["points"].sum())
    blake_points = int(baker[baker["athlete"] == "Blake"]["points"].sum())
    blake_count = int(len(baker[baker["athlete"] == "Blake"]))

    # Row1 non-strike Ball3 by different bowler is ignored for Alex's 10th bonus attempt.
    # Alex points: 75 (row1) + 39 (row2) + 40 (row3) = 154
    assert alex_points == 154
    # Blake only gets strike-fill credit on row2 and row3.
    assert blake_points == 40
    assert blake_count == 2
    assert not ((strike_attempts["ball"] == "Ball3") & ~strike_attempts["is_strike"]).any()


def test_forced_split_leaves_are_excluded_when_missed() -> None:
    df = pd.DataFrame(
        [
            {
                "Frame1Ball1Bowler": "Casey",
                "Frame1Ball1": 8,
                "Frame1Ball2": 1,
                "Frame1Ball1Pins": "1-2-10",
                "Frame1Ball1Split": 0,
                "Frame2Ball1Bowler": "Casey",
                "Frame2Ball1": 8,
                "Frame2Ball2": 2,
                "Frame2Ball1Pins": "1-3-7",
                "Frame2Ball1Split": 0,
                "Frame3Ball1Bowler": "Casey",
                "Frame3Ball1": 6,
                "Frame3Ball2": 3,
                "Frame3Ball1Pins": "1-2-4-10",
                "Frame3Ball1Split": 0,
                "Frame4Ball1Bowler": "Casey",
                "Frame4Ball1": 6,
                "Frame4Ball2": 2,
                "Frame4Ball1Pins": "1-3-6-7",
                "Frame4Ball1Split": 0,
            }
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    result = parse_bowlsheet_csv(csv_bytes)
    rec = result.records.sort_values(["frame"]).reset_index(drop=True)

    # Frame1: forced split miss excluded from attempts
    assert rec.loc[0, "pin_leave"] == "1-2-10"
    assert bool(rec.loc[0, "split"]) is True
    assert bool(rec.loc[0, "made"]) is False
    assert bool(rec.loc[0, "count_attempt"]) is False

    # Frame2: forced split made still counted
    assert rec.loc[1, "pin_leave"] == "1-3-7"
    assert bool(rec.loc[1, "split"]) is True
    assert bool(rec.loc[1, "made"]) is True
    assert bool(rec.loc[1, "count_attempt"]) is True

    # Frame3: forced split miss excluded from attempts
    assert rec.loc[2, "pin_leave"] == "1-2-4-10"
    assert bool(rec.loc[2, "split"]) is True
    assert bool(rec.loc[2, "made"]) is False
    assert bool(rec.loc[2, "count_attempt"]) is False

    # Frame4: mirrored forced split miss excluded from attempts
    assert rec.loc[3, "pin_leave"] == "1-3-6-7"
    assert bool(rec.loc[3, "split"]) is True
    assert bool(rec.loc[3, "made"]) is False
    assert bool(rec.loc[3, "count_attempt"]) is False


def test_tenth_frame_ball3_strike_attempt_context() -> None:
    df = pd.DataFrame(
        [
            {
                "Frame1Ball1Bowler": "Alex",
                "Frame1Ball1": 10,
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball3Bowler": "Alex",
                "Frame10Ball1": 10,
                "Frame10Ball2": 10,
                "Frame10Ball3": 7,
            },
            {
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball3Bowler": "Alex",
                "Frame10Ball1": 10,
                "Frame10Ball2": 7,
                "Frame10Ball3": 10,
                "Frame10Ball2Pins": "3-6-10",
            },
            {
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball3Bowler": "Alex",
                "Frame10Ball1": 9,
                "Frame10Ball2": 1,
                "Frame10Ball3": 10,
            },
            {
                "Frame10Ball1Bowler": "Alex",
                "Frame10Ball2Bowler": "Alex",
                "Frame10Ball3Bowler": "Alex",
                "Frame10Ball1": 10,
                "Frame10Ball2": 10,
                "Frame10Ball3": 10,
            },
        ]
    )

    attempts = parse_bowlsheet_csv(df.to_csv(index=False).encode("utf-8")).frame_attempts
    ball3_attempts = attempts[attempts["ball"] == "Ball3"]

    assert len(ball3_attempts) == 2
    assert ball3_attempts["is_strike"].all()
    assert set(ball3_attempts["game_id"]) == {2, 3}
