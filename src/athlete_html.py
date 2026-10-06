from __future__ import annotations

from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from src.thresholds import MULTI_PIN_THRESHOLDS, single_pin_thresholds


def build_athlete_reports_zip(
    team: str,
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None = None,
    baker_frames: pd.DataFrame | None = None,
    team_games: pd.DataFrame | None = None,
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for athlete in athlete_names(records, frame_attempts, baker_frames, team_games):
            filename = f"{_safe_filename(athlete)} - LTU Spare Report.html"
            archive.writestr(
                filename,
                build_athlete_report_html(athlete, team, records, frame_attempts, baker_frames, team_games),
            )
    return output.getvalue()


def build_athlete_report_html(
    athlete: str,
    team: str,
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None = None,
    baker_frames: pd.DataFrame | None = None,
    team_games: pd.DataFrame | None = None,
) -> bytes:
    athlete_records = _for_athlete(records, athlete)
    athlete_strikes = _for_athlete(frame_attempts, athlete)
    athlete_baker = _for_athlete(baker_frames, athlete)
    athlete_team_games = _for_athlete(team_games, athlete)

    summary = _event_summary(athlete_records, athlete_strikes, athlete_baker, athlete_team_games)
    single = _leave_summary(athlete_records, is_single=True)
    multi = _leave_summary(athlete_records, is_single=False)
    single_thresholds = single_pin_thresholds(team)

    body = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(athlete)} - LTU Spare Conversion Report</title>
  <style>
    :root {{ --navy:#17365d; --blue:#dce6f1; --border:#c8d0da; --text:#17202a; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#f3f6f9; color:var(--text); font-family:Arial, sans-serif; }}
    main {{ width:min(1300px, 94vw); margin:2rem auto; background:white; padding:2rem; box-shadow:0 8px 28px #17365d1c; }}
    h1 {{ margin:0; color:var(--navy); font-size:2rem; }}
    .subtitle {{ margin:.35rem 0 1.8rem; color:#52606d; }}
    h2 {{ margin-top:2rem; color:var(--navy); border-bottom:2px solid var(--blue); padding-bottom:.35rem; }}
    table {{ width:100%; border-collapse:collapse; margin:.75rem 0 1.5rem; font-size:.92rem; }}
    th, td {{ border:1px solid var(--border); padding:.5rem .6rem; text-align:center; }}
    th {{ background:var(--navy); color:white; }}
    th:first-child, td:first-child {{ text-align:left; }}
    .green {{ background:#c6efce; }}
    .yellow {{ background:#ffeb9c; }}
    .red {{ background:#ffc7ce; }}
    .empty {{ color:#6b7280; font-style:italic; }}
    .combination-grid {{ display:grid; grid-template-columns:minmax(0, 1fr) minmax(0, 1fr); gap:1.5rem; align-items:start; }}
    .combination-grid h2 {{ margin-top:2rem; }}
    footer {{ margin-top:2rem; color:#6b7280; font-size:.8rem; }}
    @media (max-width:800px) {{
      main {{ width:100%; margin:0; padding:1rem; }}
      .combination-grid {{ grid-template-columns:1fr; gap:0; }}
      table {{ font-size:.78rem; }}
      th,td {{ padding:.35rem; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>{escape(athlete)}</h1>
  <p class="subtitle">LTU {escape(team)} Team - Spare Conversion Report</p>
  <h2>Event Summary</h2>
  {_summary_table(summary, single_thresholds)}
  <div class="combination-grid">
    <section>
      <h2>Single-Pin Combinations</h2>
      {_leave_table(single, green_threshold=single_thresholds[0], yellow_threshold=single_thresholds[1])}
    </section>
    <section>
      <h2>Multi-Pin Combinations</h2>
      {_leave_table(multi, green_threshold=MULTI_PIN_THRESHOLDS[0], yellow_threshold=MULTI_PIN_THRESHOLDS[1])}
    </section>
  </div>
  <footer>Generated from the BowlSheet data loaded in the LTU Spare Conversion Report app.</footer>
</main>
</body>
</html>
"""
    return body.encode("utf-8")


def athlete_names(
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None = None,
    baker_frames: pd.DataFrame | None = None,
    team_games: pd.DataFrame | None = None,
) -> list[str]:
    names: set[str] = set()
    for frame in (records, frame_attempts, baker_frames, team_games):
        if frame is not None and not frame.empty and "athlete" in frame.columns:
            names.update(frame["athlete"].dropna().astype(str))
    return sorted(name for name in names if name.strip())


def _for_athlete(frame: pd.DataFrame | None, athlete: str) -> pd.DataFrame:
    if frame is None or frame.empty or "athlete" not in frame.columns:
        return pd.DataFrame()
    return frame[frame["athlete"].astype(str) == athlete].copy()


def _event_summary(
    records: pd.DataFrame,
    strikes: pd.DataFrame,
    baker: pd.DataFrame,
    team_games: pd.DataFrame,
) -> pd.DataFrame:
    events: set[str] = set()
    for frame in (records, strikes, baker, team_games):
        if not frame.empty and "event" in frame.columns:
            events.update(frame["event"].dropna().astype(str))

    rows = [_summary_row(event, records, strikes, baker, team_games) for event in events]
    rows.sort(key=lambda row: (row.pop("_sort_date"), row["Event"]))
    total = _summary_row("Total", records, strikes, baker, team_games, filter_event=False)
    total.pop("_sort_date")
    return pd.DataFrame(rows + [total])


def _summary_row(
    event: str,
    records: pd.DataFrame,
    strikes: pd.DataFrame,
    baker: pd.DataFrame,
    team_games: pd.DataFrame,
    *,
    filter_event: bool = True,
) -> dict[str, object]:
    selected_records = _event_filter(records, event) if filter_event else records
    selected_strikes = _event_filter(strikes, event) if filter_event else strikes
    selected_baker = _event_filter(baker, event) if filter_event else baker
    selected_team_games = _event_filter(team_games, event) if filter_event else team_games

    single = selected_records[selected_records.get("is_single", pd.Series(dtype=bool)) == True]  # noqa: E712
    multi = selected_records[selected_records.get("is_single", pd.Series(dtype=bool)) == False]  # noqa: E712
    single_made, single_attempts = _spare_counts(single)
    multi_made, multi_attempts = _spare_counts(multi)
    strike_attempts = len(selected_strikes)
    strike_made = int(selected_strikes["is_strike"].sum()) if not selected_strikes.empty else 0
    baker_frames = len(selected_baker)
    baker_points = int(selected_baker["points"].sum()) if not selected_baker.empty else 0
    average = selected_team_games["score"].sum() / len(selected_team_games) if len(selected_team_games) else None
    dates = []
    for frame in (selected_records, selected_strikes, selected_baker, selected_team_games):
        if not frame.empty and "game_date" in frame.columns:
            dates.extend(pd.to_datetime(frame["game_date"], errors="coerce").dropna().tolist())
    sort_date = min(dates) if dates else pd.Timestamp.max

    return {
        "Event": event,
        "SP Made": single_made,
        "SP Att": single_attempts,
        "SP %": _ratio(single_made, single_attempts),
        "MP Made": multi_made,
        "MP Att": multi_attempts,
        "MP %": _ratio(multi_made, multi_attempts),
        "STR": strike_made,
        "STR Att": strike_attempts,
        "STR %": _ratio(strike_made, strike_attempts),
        "BKR Pts": baker_points,
        "BKR Frm": baker_frames,
        "BKR Avg": _ratio(baker_points, baker_frames),
        "AVG": average,
        "_sort_date": sort_date,
    }


def _event_filter(frame: pd.DataFrame, event: str) -> pd.DataFrame:
    if frame.empty or "event" not in frame.columns:
        return frame.iloc[0:0].copy()
    return frame[frame["event"].astype(str) == event].copy()


def _spare_counts(frame: pd.DataFrame) -> tuple[int, int]:
    if frame.empty:
        return 0, 0
    attempts = frame["count_attempt"].fillna(False).astype(bool)
    made = frame["made"].fillna(False).astype(bool)
    return int((made & attempts).sum()), int(attempts.sum())


def _leave_summary(records: pd.DataFrame, *, is_single: bool) -> pd.DataFrame:
    if records.empty:
        return pd.DataFrame(columns=["Pin Leave", "Made", "Attempts", "Make %"])
    section = records[records["is_single"] == is_single].copy()
    if section.empty:
        return pd.DataFrame(columns=["Pin Leave", "Made", "Attempts", "Make %"])
    section["attempt"] = section["count_attempt"].fillna(False).astype(bool)
    section["effective_made"] = section["made"].fillna(False).astype(bool) & section["attempt"]
    grouped = (
        section.groupby("pin_leave", dropna=False)
        .agg(Made=("effective_made", "sum"), Attempts=("attempt", "sum"))
        .reset_index()
        .rename(columns={"pin_leave": "Pin Leave"})
    )
    grouped = grouped[grouped["Attempts"] > 0].copy()
    grouped["Make %"] = grouped.apply(lambda row: row["Made"] / row["Attempts"], axis=1)
    return grouped.sort_values(["Make %", "Attempts", "Pin Leave"], ascending=[True, False, True])


def _summary_table(summary: pd.DataFrame, single_thresholds: tuple[float, float]) -> str:
    if summary.empty:
        return '<p class="empty">No statistics available.</p>'
    headers = list(summary.columns)
    rows = []
    for _, row in summary.iterrows():
        cells = []
        for header in headers:
            value = row[header]
            css = ""
            if header == "SP %":
                css = _color_class(value, single_thresholds[0], single_thresholds[1])
            elif header == "MP %":
                css = _color_class(value, MULTI_PIN_THRESHOLDS[0], MULTI_PIN_THRESHOLDS[1])
            cells.append(f'<td class="{css}">{_format_value(header, value)}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return _table(headers, rows)


def _leave_table(frame: pd.DataFrame, *, green_threshold: float, yellow_threshold: float) -> str:
    if frame.empty:
        return '<p class="empty">No counted attempts.</p>'
    headers = list(frame.columns)
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for header in headers:
            css = _color_class(row[header], green_threshold, yellow_threshold) if header == "Make %" else ""
            cells.append(f'<td class="{css}">{_format_value(header, row[header])}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return _table(headers, rows)


def _table(headers: list[str], rows: list[str]) -> str:
    heading = "".join(f"<th>{escape(str(header))}</th>" for header in headers)
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _format_value(header: str, value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if header.endswith("%"):
        return f"{float(value):.2%}"
    if header == "AVG":
        return f"{float(value):.2f}"
    if header == "BKR Avg":
        return f"{float(value):.1f}"
    return escape(str(value))


def _color_class(value: object, green_threshold: float, yellow_threshold: float) -> str:
    if value is None or pd.isna(value):
        return ""
    if float(value) >= green_threshold:
        return "green"
    if float(value) >= yellow_threshold:
        return "yellow"
    return "red"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "", value).strip(" .")
    return cleaned or "Athlete"
