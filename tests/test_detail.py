from __future__ import annotations

import pandas as pd

from src.detail import (
    abbreviate_statistic_label,
    add_statistic_classification,
    render_statistic_chips,
    sort_ball_by_ball_detail,
    split_team_baker_detail,
)


def test_detail_splits_team_and_baker_without_changing_order() -> None:
    detail = pd.DataFrame(
        [
            {"Game Type": "Team", "Frame": 1},
            {"Game Type": "Baker", "Frame": 4},
            {"Game Type": "Team", "Frame": 2},
            {"Game Type": "Baker", "Frame": 9},
        ]
    )

    team, baker = split_team_baker_detail(detail)

    assert team["Frame"].tolist() == [1, 2]
    assert baker["Frame"].tolist() == [4, 9]


def test_statistic_chips_use_distinct_colors_and_escape_labels() -> None:
    expectations = {
        "Strike": ("Strike", "#70AD47"),
        "Single Pin Made": ("SP Made", "#C6E0B4"),
        "Multi Pin Made": ("MP Made", "#E2F0D9"),
        "Single Pin Missed": ("SP Missed", "#E60000"),
        "Multi Pin Missed": ("MP Missed", "#F4CCCC"),
        "Split (Excluded)": ("Split (Excluded)", "#FFF2CC"),
        "Fill (Excluded)": ("Fill (Excluded)", "#E7E6E6"),
    }

    for label, (display_label, color) in expectations.items():
        chip = render_statistic_chips(label)
        assert color in chip
        assert display_label in chip

    assert "&lt;script&gt;" in render_statistic_chips("<script>")


def test_statistic_label_abbreviations_preserve_result_details() -> None:
    assert abbreviate_statistic_label("Single Pin Made (Split)") == "SP Made (Split)"
    assert abbreviate_statistic_label("Multi Pin Missed (2)") == "MP Missed (2)"


def test_ball_detail_sorts_by_game_timestamp_then_numeric_game_order() -> None:
    detail = pd.DataFrame(
        [
            {
                "Game ID": "source:10",
                "Frame": "10",
                "Game Type": "Baker",
                "__Game Date": "October 03 2026 10:00:00",
                "__Source Order": 0,
                "__Game Order": 10,
            },
            {
                "Game ID": "source:2",
                "Frame": "2",
                "Game Type": "Team",
                "__Game Date": "October 03 2026 08:00:00",
                "__Source Order": 0,
                "__Game Order": 2,
            },
            {
                "Game ID": "source:2",
                "Frame": "1",
                "Game Type": "Team",
                "__Game Date": "October 03 2026 08:00:00",
                "__Source Order": 0,
                "__Game Order": 2,
            },
        ]
    )

    out = sort_ball_by_ball_detail(detail)

    assert list(zip(out["Game ID"], out["Frame"])) == [
        ("source:2", 1),
        ("source:2", 2),
        ("source:10", 10),
    ]
    assert not any(column.startswith("__Game") or column == "__Source Order" for column in out.columns)


def test_ball_detail_classifies_counted_statistics_and_excluded_splits() -> None:
    detail = pd.DataFrame(
        [
            {"Game ID": "g1", "Frame": 10, "Ball1": 10, "Game Type": "Team"},
            {"Game ID": "g2", "Frame": 1, "Ball1": 9, "Game Type": "Team"},
            {"Game ID": "g3", "Frame": 2, "Ball1": 8, "Game Type": "Team"},
            {"Game ID": "g4", "Frame": 3, "Ball1": 8, "Game Type": "Team"},
        ]
    )
    frame_attempts = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "frame": 10,
                "ball": ball,
                "athlete": "Alex",
                "is_strike": True,
            }
            for ball in ("Ball1", "Ball2", "Ball3")
        ]
    )
    records = pd.DataFrame(
        [
            {
                "game_id": "g2",
                "frame": 1,
                "athlete": "Alex",
                "is_single": True,
                "made": True,
                "split": False,
                "count_attempt": True,
            },
            {
                "game_id": "g3",
                "frame": 2,
                "athlete": "Alex",
                "is_single": False,
                "made": True,
                "split": True,
                "count_attempt": True,
            },
            {
                "game_id": "g4",
                "frame": 3,
                "athlete": "Alex",
                "is_single": False,
                "made": False,
                "split": True,
                "count_attempt": False,
            },
        ]
    )

    out = add_statistic_classification(detail, records, frame_attempts, "Alex")

    assert out.columns.tolist()[-2:] == ["Statistic", "Game Type"]
    assert out["Statistic"].tolist() == [
        "Strike (3)",
        "Single Pin Made",
        "Multi Pin Made (Split)",
        "Split (Excluded)",
    ]


def test_ball_detail_can_show_multiple_statistics_for_tenth_frame() -> None:
    detail = pd.DataFrame([{"Game ID": "g1", "Frame": 10, "Game Type": "Baker"}])
    frame_attempts = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "frame": 10,
                "ball": "Ball1",
                "athlete": "Alex",
                "is_strike": True,
            }
        ]
    )
    records = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "frame": 10,
                "athlete": "Alex",
                "is_single": False,
                "made": True,
                "split": False,
                "count_attempt": True,
            }
        ]
    )

    out = add_statistic_classification(detail, records, frame_attempts, "Alex")

    assert out.loc[0, "Statistic"] == "Strike / Multi Pin Made"


def test_other_bowler_non_strike_fill_is_excluded_and_only_shows_ball_three() -> None:
    detail = pd.DataFrame(
        [
            {
                "Game ID": "g1",
                "Frame": 10,
                "Points": "",
                "Ball1": 9,
                "Ball2": 1,
                "Ball3": 5,
                "Game Type": "Team",
                "__Ball1 Bowler": "Amelia",
                "__Ball2 Bowler": "Amelia",
                "__Ball3 Bowler": "Abigail",
            }
        ]
    )
    records = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "frame": 10,
                "athlete": "Amelia",
                "is_single": True,
                "made": True,
                "split": False,
                "count_attempt": True,
            }
        ]
    )

    out = add_statistic_classification(detail, records, pd.DataFrame(), "Abigail")

    assert out.loc[0, "Statistic"] == "Fill (Excluded)"
    assert out.loc[0, "Ball1"] == ""
    assert out.loc[0, "Ball2"] == ""
    assert out.loc[0, "Ball3"] == 5
    assert not any(column.startswith("__Ball") for column in out.columns)
