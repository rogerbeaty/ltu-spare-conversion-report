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
    assert ">3-6-10</div>" in html
    assert "Left:" not in html
    assert html.count("<circle") == 10
    assert "<text" not in html
    assert 'width="35" height="30.5"' in html


def test_spare_strike_missing_and_inconsistent_leaves():
    spare = pin_racks(build_pin_diagram(9, 1, leave1="10"))[0]
    assert spare["colors"]["10"] == "second"
    strike = pin_racks(build_pin_diagram(10, None))[0]
    assert set(strike["colors"].values()) == {"first"}
    assert strike["left"] == "Strike"
    strike_html = render_pin_diagram(build_pin_diagram(10, None))
    assert ">Strike</div>" in strike_html
    assert "None" not in strike_html
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


def test_pdf_pin_labels_match_screen_labels():
    from types import SimpleNamespace
    from src.athlete_pdf import PinDiagram

    labels = []
    canvas = SimpleNamespace(setFont=lambda *args: None, setFillColor=lambda *args: None,
                             circle=lambda *args, **kwargs: None,
                             drawCentredString=lambda x, y, text: labels.append(text))
    for value in (build_pin_diagram(7, 2, leave1="3-6-10", leave2="3"),
                  build_pin_diagram(10, None)):
        drawing = PinDiagram(value)
        drawing.canv = canvas
        drawing.draw()
    assert labels == ["3-6-10", "Strike"]
    legacy = build_pin_diagram(10, None).replace('"Strike"', '"None"')
    assert pin_racks(legacy)[0]["left"] == "Strike"


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
    width, height = table.wrap(549.6, 700)
    assert width <= 549.6
    assert table._colWidths[1] >= 27
    assert height < 700
    assert "Points" not in table._cellvalues[1]
    assert isinstance(table._cellvalues[2][4], (list, tuple))
    assert isinstance(table._cellvalues[2][4][0], PinDiagram)


def test_screen_detail_hides_points_without_changing_source():
    from contextlib import nullcontext
    from types import SimpleNamespace
    from src.detail import split_team_baker_detail

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_render_team_baker_detail")
    rendered = []
    def render(frame, **kwargs):
        rendered.append(frame)
        return ""
    scope = {"pd": pd, "split_team_baker_detail": split_team_baker_detail,
             "_render_table_html": render,
             "st": SimpleNamespace(caption=lambda *args: None,
                                   columns=lambda count: [nullcontext() for _ in range(count)],
                                   markdown=lambda *args, **kwargs: None)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "app.py", "exec"), scope)
    source = pd.DataFrame([
        {"Game Type": "Team", "Frame": 1, "Points": "", "Ball1": 10},
        {"Game Type": "Baker", "Frame": 1, "Points": 20, "Ball1": 10},
    ])
    scope["_render_team_baker_detail"](source)
    assert len(rendered) == 2
    assert all("Points" not in frame for frame in rendered)
    assert source.loc[1, "Points"] == 20


def test_detail_headers_wrap_without_wrapping_data_cells():
    import re
    from html import escape
    from src.detail import render_statistic_chips

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_render_table_html")
    scope = {"pd": pd, "re": re, "html_escape": escape,
             "render_statistic_chips": render_statistic_chips,
             "render_pin_diagram": render_pin_diagram}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "app.py", "exec"), scope)
    frame = pd.DataFrame([{"Game Type": "Team", "Frame": 1,
                           "Pin Diagram": build_pin_diagram(10, None), "Statistic": "Strike"}])
    html = scope["_render_table_html"](frame, compact=True, color_statistic=True)
    assert "<th>Game<br>Type</th>" in html
    assert "<th>Pin<br>Diagram</th>" in html
    assert "<td>Team</td>" in html
    assert 'width="35" height="30.5"' in html
