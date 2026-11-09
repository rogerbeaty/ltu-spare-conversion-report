"""Sortable summary with an independent, pinned totals row."""
from __future__ import annotations

import json
import re

import pandas as pd
from st_aggrid import JsCode

from src.thresholds import MULTI_PIN_THRESHOLDS

SUMMARY_LABELS = {
    "Single-Pin Made": "SP Made", "Single-Pin Attempts": "SP Att", "Single-Pin %": "SP %",
    "Multi-Pin Made": "MP Made", "Multi-Pin Attempts": "MP Att", "Multi-Pin %": "MP %",
    "Strikes": "STR", "Strike Attempts": "STR Att", "Strike %": "STR %",
    "Baker Pts": "BKR Pts", "Baker Frames": "BKR Frm", "Baker Avg": "BKR Avg",
    "Average": "AVG",
}


def build_summary_grid(summary: pd.DataFrame, single_thresholds: tuple[float, float]) -> tuple[pd.DataFrame, dict]:
    view = summary.rename(columns=SUMMARY_LABELS)
    is_total = view["Name"].eq("Total")
    # Keep raw numeric values for correct sorting and weighted totals. Format only in the browser.
    rows = view.loc[~is_total].reset_index(drop=True)
    totals = json.loads(view.loc[is_total].to_json(orient="records"))
    definitions = []
    for column in view.columns:
        definition = {
            "field": column, "headerName": column, "width": 85, "minWidth": 75,
            "cellDataType": "number" if column != "Name" else "text",
        }
        if column == "Name":
            definition.update(pinned="left", lockPinned=True, width=200, minWidth=180,
                              cellStyle={"textAlign": "left", "whiteSpace": "nowrap"})
        else:
            if column.endswith("%"):
                expression = "(Number(p.value) * 100).toFixed(2) + '%'"
            elif column == "BKR Avg":
                expression = "Number(p.value).toFixed(1)"
            elif column == "AVG":
                expression = "Number(p.value).toFixed(2)"
            elif re.fullmatch(r"Game \d+", column):
                expression = "Number(p.value).toFixed(0)"
            else:
                expression = "String(p.value)"
            definition["valueFormatter"] = JsCode(
                "function(p) { return p.value == null ? '' : " + expression + "; }"
            )
            if column in {"SP %", "MP %"}:
                green, yellow = single_thresholds if column == "SP %" else MULTI_PIN_THRESHOLDS
                definition["cellStyle"] = JsCode(
                    "function(p) { const style = {textAlign:'center', color:'#17202A'}; "
                    "if(p.value == null) return {textAlign:'center'}; "
                    f"style.backgroundColor = p.value >= {green} ? '#C6EFCE' : "
                    f"p.value >= {yellow} ? '#FFEB9C' : '#FFC7CE'; return style; }}"
                )
        definitions.append(definition)
    return rows, {
        "columnDefs": definitions,
        "defaultColDef": {
            "sortable": True, "resizable": True, "editable": False,
            "wrapHeaderText": True, "autoHeaderHeight": True,
            "cellStyle": {"textAlign": "center", "whiteSpace": "nowrap"},
        },
        "pinnedBottomRowData": totals,
        "getRowId": JsCode("function(p) { return String(p.data.Name); }"),
        "getRowStyle": JsCode("function(p) { return p.node.rowPinned ? {fontWeight:'bold'} : null; }"),
        "domLayout": "autoHeight",
        "rowHeight": 35,
        "headerHeight": 40,
        "animateRows": False,
        "suppressMovableColumns": True,
        "suppressCellFocus": False,
        "enableCellTextSelection": True,
    }
