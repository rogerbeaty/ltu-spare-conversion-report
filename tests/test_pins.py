from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from src.pins import build_pin_diagram, pin_racks, render_pin_diagram
from src.detail import add_statistic_classification


@pytest.mark.parametrize("second,final,fallen", [(2, "3", {6, 10}), (1, "6-10", {3})])
def test_lauren_missed_three_six_ten(second, final, fallen):
    value = build_pin_diagram(7, second, leave1=" 3-6-10", leave2=final)
    rack = pin_racks(value)[0]
    assert rack["left"] == "3-6-10"
    assert {int(pin) for pin, state in rack["colors"].items() if state == "second"} == fallen
    assert sum(state == "first" for state in rack["colors"].values()) == 7
    html = render_pin_diagram(value)
    assert "Left: 3-6-10" in html
    assert html.count("<circle") == 10
    assert "<text" not in html


def test_spare_strike_missing_and_inconsistent_leaves():
    spare = pin_racks(build_pin_diagram(9, 1, leave1="10"))[0]
    assert spare["colors"]["10"] == "second"
    strike = pin_racks(build_pin_diagram(10, None))[0]
    assert set(strike["colors"].values()) == {"first"}
    unknown = pin_racks(build_pin_diagram(7, 1, leave1=None))[0]
    assert set(unknown["colors"].values()) == {"unknown"}
    invalid = pin_racks(build_pin_diagram(7, 1, leave1="3-6-10", leave2="4-7"))[0]
    assert invalid["colors"]["3"] == "unknown"
    assert build_pin_diagram(None, None) == ""


def test_tenth_frame_resets_rack_without_mixing_deliveries():
    racks = pin_racks(build_pin_diagram(10, 7, 2, None, "3-6-10", "3"))
    assert len(racks) == 2
    assert racks[1]["left"] == "3-6-10"
    assert racks[1]["colors"]["3"] == "left"
    assert len(pin_racks(build_pin_diagram(10, 10, 10))) == 3
    racks = pin_racks(build_pin_diagram(9, 1, 8, "10", "", "4-7"))
    assert len(racks) == 2
    assert racks[1]["left"] == "4-7"
    assert racks[1]["colors"]["4"] == "unknown"


def test_other_bowler_fill_shows_only_the_fill_rack():
    detail = pd.DataFrame([{
        "Game ID": "g", "Frame": 10, "Game Type": "Team",
        "Ball1": 9, "Ball2": 1, "Ball3": 8,
        "Pin Diagram": build_pin_diagram(9, 1, 8, "10", "", "4-7"),
        "__Ball1 Bowler": "Other", "__Ball2 Bowler": "Other",
        "__Ball3 Bowler": "Alex", "__Ball3 Leave": "4-7",
    }])
    out = add_statistic_classification(detail, None, None, "Alex")
    racks = pin_racks(out.iloc[0]["Pin Diagram"])
    assert len(racks) == 1
    assert racks[0]["left"] == "4-7"
    assert not any(str(c).startswith("__Ball") for c in out.columns)


def _detail_builder():
    from io import BytesIO
    from src.parser import (canonicalize_athlete_name, canonicalize_event_name,
                            detect_format, preprocess_bowlsheet_export)
    scope = dict(pd=pd, BytesIO=BytesIO, build_pin_diagram=build_pin_diagram,
                 canonicalize_athlete_name=canonicalize_athlete_name,
                 canonicalize_event_name=canonicalize_event_name,
                 detect_format=detect_format, preprocess_bowlsheet_export=preprocess_bowlsheet_export)
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_read_csv_with_fallback_local", "_build_ball_by_ball_detail"}]
    exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), scope)
    return scope["_build_ball_by_ball_detail"]


def test_detail_extraction_for_both_csv_formats():
    build = _detail_builder()
    classic = pd.DataFrame([{
        "Event": "Event", "GameType": "Team", "Frame1Ball1Bowler": "Alex",
        "Frame1Ball1": 7, "Frame1Ball2": 2,
        "Frame1Ball1Pins": "3-6-10", "Frame1Ball2Pins": "3",
    }]).to_csv(index=False).encode()
    grid = [""] * 101
    grid[0], grid[2], grid[7], grid[8] = "Event", "Team", "Alex", "Alex"
    grid[59], grid[60], grid[80], grid[81] = 7, 2, "3-6-10", "3"
    delivery = pd.DataFrame([grid]).to_csv(index=False, header=False).encode()
    for source in (classic, delivery):
        out = build(source, "Event", "Alex", include_bowler_metadata=True)
        rack = pin_racks(out.iloc[0]["Pin Diagram"])[0]
        assert rack["left"] == "3-6-10"
        assert rack["colors"]["3"] == "left"
        assert rack["colors"]["6"] == "second"


def test_pin_diagrams_fit_portrait_pdf_detail():
    from src.athlete_pdf import PinDiagram, _frame_detail_pdf_table

    detail = pd.DataFrame([{
        "Game Type": "Team", "Frame": 10, "Points": "",
        "Ball1": 10, "Ball2": 7, "Ball3": 2,
        "Pin Diagram": build_pin_diagram(10, 7, 2, None, "3-6-10", "3"),
        "Statistic": "Strike / Multi Pin Missed",
    }])
    table = _frame_detail_pdf_table(detail)
    width, height = table.wrap(561.6, 700)
    assert width <= 561.6
    assert height < 700
    assert isinstance(table._cellvalues[2][5], (list, tuple))
    assert isinstance(table._cellvalues[2][5][0], PinDiagram)
