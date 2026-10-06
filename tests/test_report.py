from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

from src.report import build_report_workbook, build_summary_workbook


def test_summary_formats_baker_average_as_decimal_points() -> None:
    records = pd.DataFrame(
        [
            {
                "athlete": "Alex",
                "is_single": True,
                "count_attempt": True,
                "made": True,
                "pin_leave": "10",
            }
        ]
    )
    baker_frames = pd.DataFrame(
        [
            {"athlete": "Alex", "points": 19},
            {"athlete": "Alex", "points": 16},
        ]
    )

    workbook_bytes = build_report_workbook(records, baker_frames=baker_frames)
    workbook = load_workbook(BytesIO(workbook_bytes))
    summary = workbook["Overall Summary"]

    assert summary["D2"].number_format == "0.00%"
    assert summary["M2"].value == 17.5
    assert summary["M2"].number_format == "0.0"


def test_summary_uses_distinct_single_and_multi_pin_color_thresholds() -> None:
    records = pd.DataFrame(
        [
            {
                "athlete": "Alex",
                "is_single": True,
                "count_attempt": True,
                "made": attempt < 17,
                "pin_leave": "10",
            }
            for attempt in range(20)
        ]
        + [
            {
                "athlete": "Alex",
                "is_single": False,
                "count_attempt": True,
                "made": attempt < 12,
                "pin_leave": "2-4-5",
            }
            for attempt in range(20)
        ]
    )

    workbook = load_workbook(BytesIO(build_report_workbook(records)))
    summary = workbook["Overall Summary"]

    assert summary["D2"].value == 0.85
    assert summary["D2"].fill.fgColor.rgb == "00FFEB9C"
    assert summary["G2"].value == 0.6
    assert summary["G2"].fill.fgColor.rgb == "00FFEB9C"

    women_workbook = load_workbook(BytesIO(build_report_workbook(records, team="Women")))
    women_summary = women_workbook["Overall Summary"]
    women_athlete = women_workbook["Alex"]

    assert women_summary["D2"].value == 0.85
    assert women_summary["D2"].fill.fgColor.rgb == "00C6EFCE"
    assert women_athlete["D5"].value == 0.85
    assert women_athlete["D5"].fill.fgColor.rgb == "00C6EFCE"


def test_summary_average_uses_team_game_scores() -> None:
    records = pd.DataFrame(
        [
            {
                "athlete": "Alex",
                "is_single": True,
                "count_attempt": True,
                "made": True,
                "pin_leave": "10",
            }
        ]
    )
    team_games = pd.DataFrame(
        [
            {"athlete": "Alex", "score": 200},
            {"athlete": "Alex", "score": 180},
        ]
    )

    workbook = load_workbook(BytesIO(build_report_workbook(records, team_games=team_games)))
    summary = workbook["Overall Summary"]

    assert summary["N1"].value == "Average"
    assert summary["N2"].value == 190
    assert summary["N2"].number_format == "0.00"


def test_athlete_sheet_uses_limited_data_common_and_one_attempt_rare() -> None:
    records = pd.DataFrame(
        [
            {
                "athlete": "Alex",
                "is_single": True,
                "count_attempt": True,
                "made": made,
                "pin_leave": "10",
            }
            for made in (True, False)
        ]
        + [
            {
                "athlete": "Alex",
                "is_single": True,
                "count_attempt": True,
                "made": False,
                "pin_leave": "7",
            }
        ]
    )

    workbook = load_workbook(BytesIO(build_report_workbook(records)))
    athlete = workbook["Alex"]

    assert athlete["A3"].value == "Single Pin"
    assert athlete["A5"].value == "10"
    assert athlete["C5"].value == 2
    assert athlete["A6"].value == "— Rare combinations below —"
    assert athlete["A7"].value == "7"
    assert athlete["C7"].value == 1
    assert athlete.row_dimensions[7].hidden is True
    assert athlete["A9"].value == "Multi Pin"


def test_filtered_summary_workbook_contains_only_supplied_summary() -> None:
    summary = pd.DataFrame(
        [
            {
                "Name": "Alex",
                "Single-Pin Made": 9,
                "Single-Pin Attempts": 10,
                "Single-Pin %": 0.9,
                "Multi-Pin Made": 4,
                "Multi-Pin Attempts": 5,
                "Multi-Pin %": 0.8,
                "Strikes": 20,
                "Strike Attempts": 40,
                "Strike %": 0.5,
                "Baker Pts": 180,
                "Baker Frames": 10,
                "Baker Avg": 18.0,
                "Average": 205.25,
            }
        ]
    )

    workbook = load_workbook(BytesIO(build_summary_workbook(summary, "Men", "2026 WHAC 1")))
    worksheet = workbook["Overall Summary"]

    assert workbook.sheetnames == ["Overall Summary"]
    assert worksheet["A1"].value == "LTU Men - 2026 WHAC 1 Overall Summary"
    assert worksheet["A3"].value == "Name"
    assert worksheet["D3"].value == "SP %"
    assert worksheet["A4"].value == "Alex"
    assert worksheet["D4"].value == 0.9
    assert worksheet["D4"].number_format == "0.00%"
    assert worksheet["N4"].value == 205.25
    assert worksheet.freeze_panes == "A4"
