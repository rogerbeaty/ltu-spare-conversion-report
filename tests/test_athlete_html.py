from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd

from src.athlete_html import athlete_names, build_athlete_report_html, build_athlete_reports_zip


def _record(athlete: str, *, is_single: bool, made: bool, leave: str) -> dict[str, object]:
    return {
        "athlete": athlete,
        "event": "2026 WHAC 1",
        "game_date": "2026-09-20",
        "is_single": is_single,
        "count_attempt": True,
        "made": made,
        "pin_leave": leave,
    }


def test_athlete_html_contains_only_selected_athlete() -> None:
    records = pd.DataFrame(
        [
            _record("Alex", is_single=True, made=True, leave="10"),
            _record("Alex", is_single=False, made=False, leave="2-4-5"),
            _record("Blake", is_single=True, made=False, leave="7"),
        ]
    )

    html = build_athlete_report_html("Alex", "Men", records).decode("utf-8")

    assert "Alex" in html
    assert "Blake" not in html
    assert "2-4-5" in html
    assert 'class="red">0.00%' in html
    assert 'class="combination-grid"' in html
    assert "grid-template-columns:minmax(0, 1fr) minmax(0, 1fr)" in html


def test_athlete_names_include_all_metric_sources() -> None:
    records = pd.DataFrame([_record("Alex", is_single=True, made=True, leave="10")])
    strikes = pd.DataFrame([{"athlete": "Blake"}])
    baker = pd.DataFrame([{"athlete": "Casey"}])

    assert athlete_names(records, strikes, baker) == ["Alex", "Blake", "Casey"]


def test_athlete_zip_contains_one_private_page_per_athlete() -> None:
    records = pd.DataFrame(
        [
            _record("Alex", is_single=True, made=True, leave="10"),
            _record("Blake", is_single=False, made=False, leave="2-4-5"),
        ]
    )

    with ZipFile(BytesIO(build_athlete_reports_zip("Men", records))) as archive:
        assert archive.namelist() == [
            "Alex - LTU Spare Report.html",
            "Blake - LTU Spare Report.html",
        ]
        alex_html = archive.read("Alex - LTU Spare Report.html").decode("utf-8")
        blake_html = archive.read("Blake - LTU Spare Report.html").decode("utf-8")

    assert "Blake" not in alex_html
    assert "Alex" not in blake_html


def test_athlete_html_includes_team_game_average() -> None:
    records = pd.DataFrame([_record("Alex", is_single=True, made=True, leave="10")])
    team_games = pd.DataFrame(
        [
            {"athlete": "Alex", "event": "2026 WHAC 1", "game_date": "2026-09-20", "score": 200},
            {"athlete": "Alex", "event": "2026 WHAC 1", "game_date": "2026-09-20", "score": 180},
        ]
    )

    html = build_athlete_report_html("Alex", "Men", records, team_games=team_games).decode("utf-8")

    assert "AVG" in html
    assert "190.00" in html


def test_womens_single_pin_color_scale_uses_lower_thresholds() -> None:
    records = pd.DataFrame(
        [
            _record("Alex", is_single=True, made=made, leave="10")
            for made in (True, True, True, False)
        ]
    )

    womens_html = build_athlete_report_html("Alex", "Women", records).decode("utf-8")
    mens_html = build_athlete_report_html("Alex", "Men", records).decode("utf-8")

    assert 'class="yellow">75.00%' in womens_html
    assert 'class="red">75.00%' in mens_html
