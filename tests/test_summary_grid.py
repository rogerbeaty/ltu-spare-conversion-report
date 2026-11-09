import pandas as pd

from src.summary_grid import build_summary_grid


def test_total_is_separate_from_sortable_rows():
    summary = pd.DataFrame([
        {"Name": "Alex", "Single-Pin Made": 9, "Single-Pin %": 0.9},
        {"Name": "Blake", "Single-Pin Made": 2, "Single-Pin %": 0.5},
        {"Name": "Total", "Single-Pin Made": 11, "Single-Pin %": 11 / 14},
    ])
    rows, options = build_summary_grid(summary, (0.85, 0.75))
    assert rows.Name.tolist() == ["Alex", "Blake"]
    for column in ("Name", "SP Made", "SP %"):
        for ascending in (True, False):
            assert "Total" not in rows.sort_values(column, ascending=ascending).Name.tolist()
    assert options["pinnedBottomRowData"][0]["Name"] == "Total"
    assert options["pinnedBottomRowData"][0]["SP Made"] == 11
    assert abs(options["pinnedBottomRowData"][0]["SP %"] - 11 / 14) < 1e-9
    assert summary.Name.tolist() == ["Alex", "Blake", "Total"]


def test_grid_keeps_names_pinned_and_sorts_raw_numbers():
    summary = pd.DataFrame([
        {"Name": "Event 1", "Average": 200.25, "Game 1": 201, "Multi-Pin %": 0.8},
        {"Name": "Total", "Average": 200.25, "Game 1": None, "Multi-Pin %": 0.8},
    ])
    rows, options = build_summary_grid(summary, (0.9, 0.8))
    columns = {column["field"]: column for column in options["columnDefs"]}
    assert columns["Name"]["pinned"] == "left"
    assert columns["AVG"]["cellDataType"] == "number"
    assert rows.iloc[0]["MP %"] == 0.8
    assert options["pinnedBottomRowData"][0]["Game 1"] is None
    assert options["domLayout"] == "autoHeight"
    assert options["defaultColDef"]["sortable"] is True


def test_summary_without_total_has_no_pinned_rows():
    rows, options = build_summary_grid(pd.DataFrame([{"Name": "Alex"}]), (0.9, 0.8))
    assert rows.Name.tolist() == ["Alex"]
    assert options["pinnedBottomRowData"] == []
