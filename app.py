from __future__ import annotations

import hashlib
from html import escape as html_escape
from io import BytesIO
from pathlib import Path
import re

import pandas as pd
import streamlit as st

from src.athlete_pdf import athlete_names as pdf_athlete_names
from src.athlete_pdf import athlete_report_filename, build_athlete_pdf_reports_zip, build_athlete_report_pdf
from src.dataset import (
    ALL_SQUADS,
    ALL_TEAMS,
    JV,
    MEN,
    VARSITY,
    WOMEN,
    classify_squad_from_upload,
    classify_team_from_filename,
    combine_saved_datasets,
)
from src.detail import (
    add_statistic_classification,
    render_statistic_chips,
    sort_ball_by_ball_detail,
    split_team_baker_detail,
)
from src.parser import (
    canonicalize_athlete_name,
    canonicalize_event_name,
    detect_format,
    preprocess_bowlsheet_export,
)
from src.storage import (
    delete_all_uploaded_csvs,
    delete_uploaded_csv,
    list_uploaded_csvs,
    save_uploaded_csv_batch,
    uploaded_csv_label,
)
from src.thresholds import MULTI_PIN_THRESHOLDS, single_pin_thresholds
from src.report import add_team_game_score_columns, append_event_total_row, build_summary_workbook

UPLOAD_DIR = Path(__file__).resolve().parent / "data" / "uploads"
DATASET_SCHEMA_VERSION = b"team-name-squad-filter-v13"
SHOW_ATTEMPT_DETAIL = False
REPORT_STATE_KEYS = (
    "athlete_pdf_zip_bytes",
    "status",
    "records",
    "frame_attempts",
    "baker_frames",
    "team_games",
    "source_files",
    "team_scope",
    "uploaded_hash",
)


def _clear_report_state() -> None:
    for key in REPORT_STATE_KEYS:
        st.session_state[key] = None


def _delete_selected_upload() -> None:
    selected = st.session_state.get("selected_saved_file", "")
    if not selected:
        return
    delete_uploaded_csv(UPLOAD_DIR, selected)
    _clear_report_state()
    st.session_state["selected_saved_file"] = ""
    st.session_state["last_uploader_hash"] = None
    st.session_state["uploader_key"] += 1


def _delete_all_uploads() -> None:
    deleted_count = delete_all_uploaded_csvs(UPLOAD_DIR)
    _clear_report_state()
    st.session_state["selected_saved_file"] = ""
    st.session_state["last_uploader_hash"] = None
    st.session_state["event_filter_multi"] = []
    st.session_state["athlete_filter"] = "All Athletes"
    st.session_state["uploader_key"] += 1
    st.session_state["deleted_upload_count"] = deleted_count


def _download_filename(team: str, event: str) -> str:
    team_token = re.sub(r"[^a-z0-9]+", "_", team.casefold()).strip("_")
    if event == "All Events":
        return f"ltu_{team_token}_athlete_pdf_reports.zip"
    event_token = re.sub(r"[^a-z0-9]+", "_", event.casefold()).strip("_")
    return f"ltu_{team_token}_{event_token}_athlete_pdf_reports.zip"


def _summary_excel_filename(team: str, event: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9 ._-]+", "", f"LTU {team} - {event} - Overall Summary")
    return f"{safe_name}.xlsx"

st.set_page_config(page_title="LTU Spare Conversion Report", layout="wide")
st.markdown(
    """
    <style>
    .block-container {
      max-width: 75vw;
      margin-left: auto;
      margin-right: auto;
    }
    #MainMenu {
      visibility: hidden;
    }
    div[data-testid="stDownloadButton"] {
      display: flex;
      justify-content: flex-end;
      width: 100%;
    }
    div[data-testid="stDownloadButton"] button {
      width: 2.75rem;
      min-width: 2.75rem;
      margin-left: auto;
      padding-left: 0.65rem;
      padding-right: 0.65rem;
    }
    div[data-testid="stDownloadButton"] button p {
      font-size: 0;
    }
    div[data-testid="stMultiSelect"] [data-baseweb="tag"] {
      background-color: #1f5a94 !important;
      color: #ffffff !important;
    }
    div[data-testid="stMultiSelect"] [data-baseweb="tag"]:hover {
      background-color: #174573 !important;
    }
    div[data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    div[data-testid="stMultiSelect"] [data-baseweb="tag"] svg {
      color: #ffffff !important;
      fill: #ffffff !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
header_slot = st.empty()

if "athlete_pdf_zip_bytes" not in st.session_state:
    st.session_state["athlete_pdf_zip_bytes"] = None
if "status" not in st.session_state:
    st.session_state["status"] = None
if "records" not in st.session_state:
    st.session_state["records"] = None
if "frame_attempts" not in st.session_state:
    st.session_state["frame_attempts"] = None
if "baker_frames" not in st.session_state:
    st.session_state["baker_frames"] = None
if "team_games" not in st.session_state:
    st.session_state["team_games"] = None
if "source_files" not in st.session_state:
    st.session_state["source_files"] = []
if "team_scope" not in st.session_state:
    st.session_state["team_scope"] = ALL_TEAMS
if "uploaded_hash" not in st.session_state:
    st.session_state["uploaded_hash"] = None
if "last_uploader_hash" not in st.session_state:
    st.session_state["last_uploader_hash"] = None
if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

uploaded_files = st.file_uploader(
    "Upload BowlSheet CSVs",
    type=["csv"],
    accept_multiple_files=True,
    key=f"csv_uploader_{st.session_state['uploader_key']}",
)

if uploaded_files:
    upload_entries = [
        (uploaded.name, uploaded.getvalue())
        for uploaded in uploaded_files
    ]
    batch_hash = hashlib.sha256()
    for original_name, uploaded_bytes in upload_entries:
        batch_hash.update(original_name.encode("utf-8"))
        batch_hash.update(b"\0")
        batch_hash.update(uploaded_bytes)
        batch_hash.update(b"\0")
    uploader_hash = batch_hash.hexdigest()
    if st.session_state.get("last_uploader_hash") != uploader_hash:
        stored_paths = save_uploaded_csv_batch(UPLOAD_DIR, upload_entries)
        st.session_state["selected_saved_file"] = stored_paths[-1].name
        st.session_state["last_uploader_hash"] = uploader_hash

saved_files = list_uploaded_csvs(UPLOAD_DIR)
saved_names = [path.name for path in saved_files]
saved_options = [""] + saved_names
if "selected_saved_file" not in st.session_state:
    st.session_state["selected_saved_file"] = saved_names[0] if saved_names else ""
elif st.session_state["selected_saved_file"] not in saved_options:
    st.session_state["selected_saved_file"] = saved_names[0] if saved_names else ""

available_teams = {classify_team_from_filename(path.name) for path in saved_files}
team_options = [team for team in (MEN, WOMEN) if team in available_teams]
if not team_options:
    team_options = [MEN, WOMEN]
if st.session_state.get("team_data_scope") not in team_options:
    st.session_state["team_data_scope"] = team_options[0]
team_scope = st.session_state["team_data_scope"]

file_squads = {path: classify_squad_from_upload(path) for path in saved_files}
team_paths = [
    path
    for path in saved_files
    if team_scope == ALL_TEAMS or classify_team_from_filename(path.name) == team_scope
]
available_squads = {file_squads[path] for path in team_paths}
squad_options = [ALL_SQUADS] + [
    squad
    for squad in (VARSITY, JV)
    if squad in available_squads
]
if st.session_state.get("squad_data_scope") not in squad_options:
    st.session_state["squad_data_scope"] = ALL_SQUADS
squad_scope = st.session_state["squad_data_scope"]

saved_col, delete_col, delete_all_col = st.columns([4, 1, 1])
with saved_col:
    selected_saved_file = st.selectbox(
        "Manage saved file",
        options=saved_options,
        key="selected_saved_file",
        format_func=lambda value: "No saved file selected"
        if not value
        else uploaded_csv_label(UPLOAD_DIR / value),
    )
with delete_col:
    st.write("")
    st.write("")
    st.button(
        "Delete saved file",
        disabled=not selected_saved_file,
        on_click=_delete_selected_upload,
    )
with delete_all_col:
    st.write("")
    st.write("")
    with st.popover(
        "Delete all data",
        disabled=not saved_files,
        use_container_width=True,
    ):
        st.warning("This permanently deletes every saved CSV and clears the report.")
        st.button(
            "Confirm delete all data",
            type="primary",
            on_click=_delete_all_uploads,
            use_container_width=True,
        )

deleted_upload_count = st.session_state.pop("deleted_upload_count", None)
if deleted_upload_count is not None:
    st.success(f"Deleted {deleted_upload_count} saved file(s). The report is now empty.")

selected_paths = [
    path
    for path in team_paths
    if squad_scope == ALL_SQUADS or file_squads[path] == squad_scope
]
if selected_paths:
    dataset_hash = hashlib.sha256()
    dataset_hash.update(DATASET_SCHEMA_VERSION)
    dataset_hash.update(team_scope.encode("utf-8"))
    dataset_hash.update(squad_scope.encode("utf-8"))
    for path in sorted(selected_paths, key=lambda item: item.name):
        dataset_hash.update(path.name.encode("utf-8"))
        dataset_hash.update(path.read_bytes())
    combined_hash = dataset_hash.hexdigest()

    if st.session_state.get("uploaded_hash") != combined_hash:
        combined = combine_saved_datasets(selected_paths, team_scope)
        event_values: set[str] = set()
        athlete_values: set[str] = set()
        for frame in (combined.records, combined.frame_attempts, combined.baker_frames, combined.team_games):
            if not frame.empty and "event" in frame.columns:
                event_values.update(frame["event"].dropna().astype(str))
            if not frame.empty and "athlete" in frame.columns:
                athlete_values.update(frame["athlete"].dropna().astype(str))
        athlete_pdf_zip_bytes = build_athlete_pdf_reports_zip(
            team_scope,
            combined.records,
            combined.frame_attempts,
            combined.baker_frames,
            combined.team_games,
        )

        st.session_state["athlete_pdf_zip_bytes"] = athlete_pdf_zip_bytes
        st.session_state["status"] = {
            "events_found": len(event_values),
            "athletes_found": len(athlete_values),
            "files_loaded": len(combined.source_files),
        }
        st.session_state["records"] = combined.records.copy()
        st.session_state["frame_attempts"] = combined.frame_attempts.copy()
        st.session_state["baker_frames"] = combined.baker_frames.copy()
        st.session_state["team_games"] = combined.team_games.copy()
        st.session_state["source_files"] = combined.source_files
        st.session_state["team_scope"] = team_scope
        st.session_state["uploaded_hash"] = combined_hash
else:
    _clear_report_state()

status = st.session_state.get("status")
with header_slot.container():
    title_col, info_col = st.columns([8, 2])
    with title_col:
        st.title("LTU Spare Conversion Report")
    with info_col:
        st.write("")
        with st.popover("Report Info"):
            st.markdown(f"**Selected men/women:** {team_scope}")
            st.markdown(f"**Selected team:** {squad_scope}")
            st.write(f"Total saved files: {len(saved_files)}")
            if status:
                st.write(f"Files used for selected team: {status['files_loaded']}")
                st.write(f"Distinct events: {status['events_found']}")
                st.write(f"Distinct athletes: {status['athletes_found']}")
            else:
                st.write("No report data is currently loaded.")

def _build_summary_preview(
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None,
    baker_frames: pd.DataFrame | None,
    team_games: pd.DataFrame | None,
    athlete_filter: str | None = None,
    group_by_event_for_athlete: bool = False,
    include_total_row: bool = True,
) -> pd.DataFrame:
    no_records = records is None or records.empty
    no_attempts = frame_attempts is None or frame_attempts.empty
    no_baker = baker_frames is None or baker_frames.empty
    no_team_games = team_games is None or team_games.empty
    if no_records and no_attempts and no_baker and no_team_games:
        return pd.DataFrame(
            columns=[
                "Name",
                "Single-Pin Made",
                "Single-Pin Attempts",
                "Single-Pin %",
                "Multi-Pin Made",
                "Multi-Pin Attempts",
                "Multi-Pin %",
                "Strikes",
                "Strike Attempts",
                "Strike %",
                "Baker Pts",
                "Baker Frames",
                "Baker Avg",
                "Average",
            ]
        )

    rows: list[dict[str, object]] = []

    if athlete_filter is not None:
        athlete_records = records[records["athlete"] == athlete_filter].copy()
        if group_by_event_for_athlete:
            event_names = set(athlete_records["event"].dropna().astype(str))
            for frame in (frame_attempts, baker_frames, team_games):
                if frame is not None and not frame.empty:
                    athlete_frame = frame[frame["athlete"] == athlete_filter]
                    event_names.update(athlete_frame["event"].dropna().astype(str))
            buckets = sorted(event_names)
        else:
            buckets = [None]
        for event_bucket in buckets:
            subset = athlete_records.copy() if event_bucket is None else athlete_records[athlete_records["event"] == event_bucket].copy()
            label = str(event_bucket) if event_bucket is not None else athlete_filter
            row_data = _build_summary_row(
                label=label,
                athlete=athlete_filter,
                records_subset=subset,
                frame_attempts=frame_attempts,
                baker_frames=baker_frames,
                team_games=team_games,
                event_bucket=event_bucket,
            )
            game_dates = list(subset["game_date"]) if "game_date" in subset.columns else []
            for frame in (frame_attempts, baker_frames, team_games):
                if frame is None or frame.empty or "game_date" not in frame.columns:
                    continue
                date_frame = frame[frame["athlete"] == athlete_filter]
                if event_bucket is not None:
                    date_frame = date_frame[date_frame["event"] == event_bucket]
                game_dates.extend(date_frame["game_date"].tolist())
            game_dates = pd.Series(game_dates, dtype="object")
            parsed_dates = pd.to_datetime(game_dates, errors="coerce")
            row_data["__sort_date"] = parsed_dates.min() if len(parsed_dates) and parsed_dates.notna().any() else pd.NaT
            rows.append(row_data)
        out = pd.DataFrame(rows)
        if not out.empty:
            out = out.sort_values(["__sort_date", "Name"], ascending=[True, True], na_position="last").reset_index(drop=True)
            out = out.drop(columns=["__sort_date"])
            if include_total_row:
                out = _append_summary_total_row(out, team_games, athlete_filter)
        return out

    athlete_names: set[str] = set(records["athlete"].dropna().astype(str))
    for frame in (frame_attempts, baker_frames, team_games):
        if frame is not None and not frame.empty:
            athlete_names.update(frame["athlete"].dropna().astype(str))
    athletes = sorted(athlete_names)
    for athlete in athletes:
        subset = records[records["athlete"] == athlete]
        rows.append(
            _build_summary_row(
                label=athlete,
                athlete=athlete,
                records_subset=subset,
                frame_attempts=frame_attempts,
                baker_frames=baker_frames,
                team_games=team_games,
                event_bucket=None,
            )
        )

    return pd.DataFrame(rows)


def _append_summary_total_row(
    df: pd.DataFrame,
    team_games: pd.DataFrame | None,
    athlete: str,
) -> pd.DataFrame:
    if df.empty:
        return df

    total_sp_made = int(df["Single-Pin Made"].sum())
    total_sp_att = int(df["Single-Pin Attempts"].sum())
    total_mp_made = int(df["Multi-Pin Made"].sum())
    total_mp_att = int(df["Multi-Pin Attempts"].sum())
    total_strikes = int(df["Strikes"].sum())
    total_str_att = int(df["Strike Attempts"].sum())
    total_bkr_pts = int(df["Baker Pts"].sum())
    total_bkr_frames = int(df["Baker Frames"].sum())
    average = None
    if team_games is not None and not team_games.empty:
        athlete_games = team_games[team_games["athlete"] == athlete]
        average = athlete_games["score"].sum() / len(athlete_games) if len(athlete_games) else None

    total_row = {
        "Name": "Total",
        "Single-Pin Made": total_sp_made,
        "Single-Pin Attempts": total_sp_att,
        "Single-Pin %": (total_sp_made / total_sp_att) if total_sp_att else None,
        "Multi-Pin Made": total_mp_made,
        "Multi-Pin Attempts": total_mp_att,
        "Multi-Pin %": (total_mp_made / total_mp_att) if total_mp_att else None,
        "Strikes": total_strikes,
        "Strike Attempts": total_str_att,
        "Strike %": (total_strikes / total_str_att) if total_str_att else None,
        "Baker Pts": total_bkr_pts,
        "Baker Frames": total_bkr_frames,
        "Baker Avg": (total_bkr_pts / total_bkr_frames) if total_bkr_frames else None,
        "Average": average,
    }

    return pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)


def _build_strike_attempt_detail(frame_attempts: pd.DataFrame | None, athlete: str) -> pd.DataFrame:
    cols = ["Event", "Date", "Game Type", "Frame", "Ball", "Pins", "Result"]
    if frame_attempts is None or frame_attempts.empty:
        return pd.DataFrame(columns=cols)

    detail = frame_attempts[frame_attempts["athlete"] == athlete].copy()
    if detail.empty:
        return pd.DataFrame(columns=cols)

    detail["Event"] = detail["event"].fillna("").astype(str)
    detail["Date"] = detail["game_date"].fillna("").astype(str)
    detail["Game Type"] = detail["game_type"].fillna("").astype(str)
    detail["Frame"] = detail["frame"]
    detail["Ball"] = detail["ball"]
    detail["Pins"] = pd.to_numeric(detail["pins"], errors="coerce").apply(
        lambda value: "" if pd.isna(value) else int(value)
    )
    detail["Result"] = detail["is_strike"].map({True: "Strike", False: "Miss"})
    detail["__date"] = pd.to_datetime(detail["game_date"], errors="coerce")
    ball_order = {"Ball1": 1, "Ball2": 2, "Ball3": 3}
    detail["__ball"] = detail["ball"].map(ball_order).fillna(0)
    detail = detail.sort_values(["__date", "game_id", "frame", "__ball"], na_position="last")
    return detail[cols].reset_index(drop=True)


def _build_summary_row(
    label: str,
    athlete: str,
    records_subset: pd.DataFrame,
    frame_attempts: pd.DataFrame | None,
    baker_frames: pd.DataFrame | None,
    team_games: pd.DataFrame | None,
    event_bucket: str | None,
) -> dict[str, object]:
    subset = records_subset
    multi = subset[~subset["is_single"]].copy()
    single = subset[subset["is_single"]].copy()

    multi_attempts = int(multi["count_attempt"].sum())
    multi_made = int((multi["made"] & multi["count_attempt"]).sum())
    single_attempts = int(single["count_attempt"].sum())
    single_made = int((single["made"] & single["count_attempt"]).sum())
    baker_points = 0
    baker_count = 0
    baker_avg = None
    if baker_frames is not None and not baker_frames.empty:
        baker_subset = baker_frames[baker_frames["athlete"] == athlete].copy()
        if event_bucket is not None:
            baker_subset = baker_subset[baker_subset["event"] == event_bucket].copy()
        baker_count = int(len(baker_subset))
        baker_points = int(baker_subset["points"].sum())
        baker_avg = (baker_points / baker_count) if baker_count else None
    strikes = 0
    strike_attempts = 0
    strike_pct = None
    if frame_attempts is not None and not frame_attempts.empty:
        strike_subset = frame_attempts[frame_attempts["athlete"] == athlete].copy()
        if event_bucket is not None:
            strike_subset = strike_subset[strike_subset["event"] == event_bucket].copy()
        strike_attempts = int(len(strike_subset))
        strikes = int(strike_subset["is_strike"].sum())
        strike_pct = (strikes / strike_attempts) if strike_attempts else None
    average = None
    if team_games is not None and not team_games.empty:
        team_subset = team_games[team_games["athlete"] == athlete].copy()
        if event_bucket is not None:
            team_subset = team_subset[team_subset["event"] == event_bucket].copy()
        if not team_subset.empty:
            average = team_subset["score"].sum() / len(team_subset)

    return {
        "Name": label,
        "Single-Pin Made": single_made,
        "Single-Pin Attempts": single_attempts,
        "Single-Pin %": (single_made / single_attempts) if single_attempts else None,
        "Multi-Pin Made": multi_made,
        "Multi-Pin Attempts": multi_attempts,
        "Multi-Pin %": (multi_made / multi_attempts) if multi_attempts else None,
        "Strikes": strikes,
        "Strike Attempts": strike_attempts,
        "Strike %": strike_pct,
        "Baker Pts": baker_points,
        "Baker Frames": baker_count,
        "Baker Avg": baker_avg,
        "Average": average,
    }


def _build_leave_preview(records: pd.DataFrame, athlete: str | None, is_single: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    section = records[records["is_single"] == is_single].copy()
    if athlete is not None:
        section = section[section["athlete"] == athlete].copy()
    if section.empty:
        empty = pd.DataFrame(columns=["Pin Leave", "Made", "Attempts", "Make %"])
        return empty, empty

    section["effective_made"] = (section["made"] & section["count_attempt"]).astype(int)
    section["count_attempt"] = section["count_attempt"].astype(int)

    grouped = (
        section.groupby("pin_leave", dropna=False)
        .agg(Made=("effective_made", "sum"), Attempts=("count_attempt", "sum"))
        .reset_index()
        .rename(columns={"pin_leave": "Pin Leave"})
    )

    grouped["Make %"] = grouped.apply(
        lambda r: (r["Made"] / r["Attempts"]) if r["Attempts"] else None,
        axis=1,
    )
    grouped = grouped[grouped["Attempts"] > 0].copy()
    grouped = grouped.sort_values(["Attempts", "Pin Leave"], ascending=[False, True])

    common = grouped[grouped["Attempts"] >= 10].copy()
    common = common.sort_values(["Make %", "Attempts", "Pin Leave"], ascending=[True, False, True])
    rare = grouped[grouped["Attempts"] < 10].copy()
    rare = rare.sort_values(["Make %", "Attempts", "Pin Leave"], ascending=[True, False, True])
    if common.empty and not rare.empty:
        limited_common = rare[rare["Attempts"] > 1].copy()
        one_attempt_rare = rare[rare["Attempts"] == 1].copy()
        return limited_common, one_attempt_rare
    return common, rare


def _uses_limited_data_rule(common: pd.DataFrame, rare: pd.DataFrame) -> bool:
    return (not common.empty and (common["Attempts"] < 10).all()) or (
        common.empty and not rare.empty and (rare["Attempts"] == 1).all()
    )


def _common_combinations_caption(common: pd.DataFrame, rare: pd.DataFrame) -> str:
    if _uses_limited_data_rule(common, rare):
        return "Common (limited data)"
    return "Common (Attempts >= 10)"


def _rare_combinations_label(label: str, common: pd.DataFrame, rare: pd.DataFrame) -> str:
    threshold = "1 attempt" if _uses_limited_data_rule(common, rare) else "Attempts < 10"
    return f"Rare {label} ({threshold})"


def _build_leave_preview_all(records: pd.DataFrame, athlete: str | None, is_single: bool) -> pd.DataFrame:
    section = records[records["is_single"] == is_single].copy()
    if athlete is not None:
        section = section[section["athlete"] == athlete].copy()
    if section.empty:
        return pd.DataFrame(columns=["Pin Leave", "Made", "Attempts", "Make %"])

    section["effective_made"] = (section["made"] & section["count_attempt"]).astype(int)
    section["count_attempt"] = section["count_attempt"].astype(int)

    grouped = (
        section.groupby("pin_leave", dropna=False)
        .agg(Made=("effective_made", "sum"), Attempts=("count_attempt", "sum"))
        .reset_index()
        .rename(columns={"pin_leave": "Pin Leave"})
    )
    grouped["Make %"] = grouped.apply(
        lambda r: (r["Made"] / r["Attempts"]) if r["Attempts"] else None,
        axis=1,
    )
    grouped = grouped[grouped["Attempts"] > 0].copy()
    return grouped.sort_values(["Make %", "Attempts", "Pin Leave"], ascending=[True, False, True])


def _read_csv_with_fallback_local(csv_bytes: bytes, header: int | None = 0) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return pd.read_csv(BytesIO(csv_bytes), encoding=enc, header=header)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(BytesIO(csv_bytes), encoding="utf-8", encoding_errors="replace", header=header)


def _build_ball_by_ball_detail(
    csv_bytes: bytes | None,
    selected_event: str,
    selected_athlete: str,
    include_event: bool = False,
    include_athlete: bool = False,
    team_vs_baker_view: bool = False,
    include_bowler_metadata: bool = False,
) -> pd.DataFrame:
    base_cols = [
        "Game ID",
        "Frame",
        "Points",
        "Ball1",
        "Ball2",
        "Ball3",
        "Game Type",
        "__Game Date",
        "__Game Order",
    ]
    if include_bowler_metadata:
        base_cols += ["__Ball1 Bowler", "__Ball2 Bowler", "__Ball3 Bowler"]
    prefix_cols = (["Event"] if include_event else []) + (["Athlete"] if include_athlete else [])
    cols = prefix_cols + base_cols
    if not csv_bytes:
        return pd.DataFrame(columns=cols)

    csv_bytes = preprocess_bowlsheet_export(csv_bytes)
    fmt = detect_format(csv_bytes)
    rows: list[dict[str, object]] = []
    selected_event = str(canonicalize_event_name(selected_event))
    selected_athlete = str(canonicalize_athlete_name(selected_athlete))
    filter_event = selected_event != "All Events"
    filter_athlete = bool(selected_athlete) and selected_athlete != "All Athletes"

    if fmt == "Frame-columns":
        df = _read_csv_with_fallback_local(csv_bytes, header=0)
        for game_id, (_, row) in enumerate(df.iterrows()):
            event = str(canonicalize_event_name(row.get("Event", "")))
            if filter_event and event != selected_event:
                continue
            game_type = str(row.get("GameType", "")).strip()
            game_date = str(row.get("Date", "")).strip()
            for frame in range(1, 11):
                b1_bowler = str(canonicalize_athlete_name(row.get(f"Frame{frame}Ball1Bowler", "")))
                b2_bowler = str(canonicalize_athlete_name(row.get(f"Frame{frame}Ball2Bowler", "")))
                b3_bowler = (
                    str(canonicalize_athlete_name(row.get("Frame10Ball3Bowler", "")))
                    if frame == 10
                    else ""
                )
                athlete = b1_bowler or b2_bowler or b3_bowler
                if filter_athlete and selected_athlete not in {b1_bowler, b2_bowler, b3_bowler}:
                    continue
                out_row = {
                    "Game ID": int(game_id),
                    "Frame": frame,
                    "Points": "",
                    "Ball1": row.get(f"Frame{frame}Ball1"),
                    "Ball2": row.get(f"Frame{frame}Ball2"),
                    "Ball3": row.get("Frame10Ball3") if frame == 10 else "",
                    "Game Type": game_type,
                    "__Game Date": game_date,
                    "__Game Order": int(game_id),
                }
                if include_bowler_metadata:
                    out_row.update(
                        {
                            "__Ball1 Bowler": b1_bowler,
                            "__Ball2 Bowler": b2_bowler,
                            "__Ball3 Bowler": b3_bowler,
                        }
                    )
                if include_athlete:
                    out_row["Athlete"] = athlete
                if include_event:
                    out_row["Event"] = event
                rows.append(out_row)
    else:
        df = _read_csv_with_fallback_local(csv_bytes, header=None)
        for game_id, (_, row) in enumerate(df.iterrows()):
            event = str(canonicalize_event_name(row.get(0, "")))
            if filter_event and event != selected_event:
                continue
            game_type = str(row.get(2, "")).strip()
            game_date = str(row.get(3, "")).strip()
            bowlers = [str(canonicalize_athlete_name(row.get(i, ""))) for i in range(7, 28)]
            knocked = [row.get(i) for i in range(59, 80)]
            for frame in range(1, 10):
                i = (frame - 1) * 2
                b1_bowler, b2_bowler = bowlers[i], bowlers[i + 1]
                athlete = b1_bowler or b2_bowler
                if filter_athlete and selected_athlete not in {b1_bowler, b2_bowler}:
                    continue
                out_row = {
                    "Game ID": int(game_id),
                    "Frame": frame,
                    "Points": "",
                    "Ball1": knocked[i],
                    "Ball2": knocked[i + 1],
                    "Ball3": "",
                    "Game Type": game_type,
                    "__Game Date": game_date,
                    "__Game Order": int(game_id),
                }
                if include_bowler_metadata:
                    out_row.update(
                        {
                            "__Ball1 Bowler": b1_bowler,
                            "__Ball2 Bowler": b2_bowler,
                            "__Ball3 Bowler": "",
                        }
                    )
                if include_athlete:
                    out_row["Athlete"] = athlete
                if include_event:
                    out_row["Event"] = event
                rows.append(out_row)
            b1_bowler, b2_bowler, b3_bowler = bowlers[18], bowlers[19], bowlers[20]
            athlete = b1_bowler or b2_bowler or b3_bowler
            if (not filter_athlete) or (selected_athlete in {b1_bowler, b2_bowler, b3_bowler}):
                out_row = {
                    "Game ID": int(game_id),
                    "Frame": 10,
                    "Points": "",
                    "Ball1": knocked[18],
                    "Ball2": knocked[19],
                    "Ball3": knocked[20],
                    "Game Type": game_type,
                    "__Game Date": game_date,
                    "__Game Order": int(game_id),
                }
                if include_bowler_metadata:
                    out_row.update(
                        {
                            "__Ball1 Bowler": b1_bowler,
                            "__Ball2 Bowler": b2_bowler,
                            "__Ball3 Bowler": b3_bowler,
                        }
                    )
                if include_athlete:
                    out_row["Athlete"] = athlete
                if include_event:
                    out_row["Event"] = event
                rows.append(out_row)

    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out

    for ball_col in ("Ball1", "Ball2", "Ball3"):
        if ball_col in out.columns:
            numeric = pd.to_numeric(out[ball_col], errors="coerce")
            out[ball_col] = numeric.apply(lambda v: "" if pd.isna(v) else int(v))

    is_baker = out["Game Type"].astype(str).str.contains("baker", case=False, na=False)
    b1 = pd.to_numeric(out["Ball1"], errors="coerce")
    b2 = pd.to_numeric(out["Ball2"], errors="coerce")
    points = []
    for baker, x, y in zip(is_baker.tolist(), b1.tolist(), b2.tolist(), strict=False):
        if not baker or pd.isna(x):
            points.append("")
            continue
        if x == 10:
            points.append(20)
        elif not pd.isna(y) and (x + y == 10):
            points.append(int(10 + x))
        else:
            points.append(int(x + (0 if pd.isna(y) else y)))
    out["Points"] = points

    if team_vs_baker_view:
        display_cols = ["Points", "Ball1", "Ball2", "Ball3"]
        out[display_cols] = out[display_cols].astype(object)
        out.loc[is_baker, ["Ball1", "Ball2", "Ball3"]] = ""
        out.loc[~is_baker, "Points"] = ""

    sort_cols = ["Game ID", "Frame"] + (["Athlete"] if include_athlete else [])
    return out.sort_values(sort_cols, ascending=[True] * len(sort_cols)).reset_index(drop=True)


def _build_combined_ball_by_ball_detail(
    source_files: list[str],
    team_scope: str,
    selected_event: str,
    selected_athlete: str,
    include_event: bool = False,
    include_athlete: bool = False,
    team_vs_baker_view: bool = False,
    include_bowler_metadata: bool = False,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_order, stored_name in enumerate(source_files):
        path = UPLOAD_DIR / stored_name
        if not path.exists():
            continue

        actual_athlete = selected_athlete

        detail = _build_ball_by_ball_detail(
            path.read_bytes(),
            selected_event=selected_event,
            selected_athlete=actual_athlete,
            include_event=include_event,
            include_athlete=include_athlete,
            team_vs_baker_view=team_vs_baker_view,
            include_bowler_metadata=include_bowler_metadata,
        )
        if detail.empty:
            continue

        source_key = stored_name.partition("__")[0][:12]
        detail["Game ID"] = source_key + ":" + detail["Game ID"].astype(str)
        detail["__Source Order"] = source_order
        if include_athlete:
            if actual_athlete != "All Athletes":
                detail["Athlete"] = selected_athlete
        frames.append(detail)

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return sort_ball_by_ball_detail(combined, include_athlete=include_athlete)


def _build_spare_attempt_detail(
    records: pd.DataFrame,
    frame_detail: pd.DataFrame,
    athlete: str,
    is_single: bool,
) -> pd.DataFrame:
    cols = [
        "Event",
        "Date",
        "Game Type",
        "Frame",
        "Pin Leave",
        "Ball1",
        "Ball2",
        "Ball3",
        "Split",
        "Result",
    ]
    section = records[
        (records["athlete"] == athlete)
        & (records["is_single"] == is_single)
        & records["count_attempt"]
    ].copy()
    if section.empty:
        return pd.DataFrame(columns=cols)

    balls = frame_detail[["Game ID", "Frame", "Game Type", "Ball1", "Ball2", "Ball3"]].drop_duplicates(
        subset=["Game ID", "Frame"]
    )
    section = section.merge(
        balls,
        left_on=["game_id", "frame"],
        right_on=["Game ID", "Frame"],
        how="left",
    )
    section["Event"] = section["event"].fillna("").astype(str)
    section["Date"] = section["game_date"].fillna("").astype(str)
    section["Pin Leave"] = section["pin_leave"].fillna("").astype(str)
    section["Split"] = section["split"].map({True: "Yes", False: "No"})
    section["Result"] = section["made"].map({True: "Made", False: "Missed"})
    section["__date"] = pd.to_datetime(section["game_date"], errors="coerce")
    section = section.sort_values(["__date", "game_id", "frame"], na_position="last")
    return section[cols].reset_index(drop=True)


def _render_table_html(
    df: pd.DataFrame,
    *,
    color_make_pct: bool = False,
    make_pct_thresholds: tuple[float, float] = (0.9, 0.8),
    color_pct_columns: dict[str, tuple[float, float]] | None = None,
    color_statistic: bool = False,
    compact: bool = False,
) -> str:
    if df.empty:
        return "<p>No data available.</p>"

    view = df.copy()

    for col in view.columns:
        if str(col) in {"Average", "AVG"}:
            view[col] = view[col].apply(lambda v: "" if pd.isna(v) else f"{v:.2f}")
        elif str(col) in {"Baker Avg", "BKR Avg"}:
            view[col] = view[col].apply(lambda v: "" if pd.isna(v) else f"{v:.1f}")
        elif re.fullmatch(r"Game \d+", str(col)):
            view[col] = view[col].apply(
                lambda v: "" if pd.isna(v) else str(int(v)) if float(v).is_integer() else str(v)
            )
        elif str(col).endswith("%"):
            view[col] = view[col].apply(lambda v: "" if pd.isna(v) else f"{v:.2%}")

    pct_thresholds = dict(color_pct_columns or {})
    html_columns: set[str] = set()
    if color_make_pct:
        pct_thresholds["Make %"] = make_pct_thresholds

    def colorize(value: str, raw_value: object, thresholds: tuple[float, float]) -> str:
        if value == "" or raw_value is None or pd.isna(raw_value):
            return ""
        green_threshold, yellow_threshold = thresholds
        if raw_value >= green_threshold:
            bg = "#C6EFCE"
        elif raw_value >= yellow_threshold:
            bg = "#FFEB9C"
        else:
            bg = "#FFC7CE"
        return f"<span style='background-color:{bg};display:block;padding:0.1rem 0.2rem;border-radius:2px'>{value}</span>"

    for column, thresholds in pct_thresholds.items():
        if column not in view.columns:
            continue
        view[column] = [
            colorize(formatted, raw, thresholds)
            for formatted, raw in zip(view[column].tolist(), df[column].tolist(), strict=False)
        ]
        html_columns.add(column)

    if color_statistic and "Statistic" in view.columns:
        view["Statistic"] = view["Statistic"].apply(render_statistic_chips)
        html_columns.add("Statistic")

    table_classes = "summary-table compact-table" if compact else "summary-table"
    if html_columns:
        for column in view.columns:
            if column in html_columns:
                continue
            view[column] = view[column].apply(
                lambda value: html_escape(value) if isinstance(value, str) else value
            )
        table_html = view.to_html(index=False, classes=table_classes, border=0, escape=False)
    else:
        table_html = view.to_html(index=False, classes=table_classes, border=0, escape=True)

    wrap_classes = "summary-wrap compact-table-wrap" if compact else "summary-wrap"

    return f"""
<style>
.summary-wrap {{
  width: calc(100% + 50px);
  max-width: calc(100vw - 2rem);
}}
.summary-table {{
  width: 100%;
  table-layout: auto;
  border-collapse: collapse;
  font-size: 0.88rem;
}}
.summary-wrap.compact-table-wrap {{
  width: fit-content;
  max-width: 100%;
}}
.summary-table.compact-table {{
  width: auto;
}}
.summary-table th, .summary-table td {{
  border: 1px solid #d9d9d9;
  padding: 0.4rem 0.5rem;
  white-space: nowrap;
  word-break: normal;
  vertical-align: top;
  text-align: center;
}}
.summary-table th {{
  font-weight: 600;
}}
.summary-table th:first-child, .summary-table td:first-child {{
  text-align: left;
}}
</style>
<div class=\"{wrap_classes}\">
{table_html}
</div>
"""


def _build_summary_grid(
    df: pd.DataFrame,
    single_thresholds: tuple[float, float],
) -> tuple[pd.io.formats.style.Styler, dict[str, object]]:
    view = df.rename(
        columns={
            "Single-Pin Made": "SP Made",
            "Single-Pin Attempts": "SP Att",
            "Single-Pin %": "SP %",
            "Multi-Pin Made": "MP Made",
            "Multi-Pin Attempts": "MP Att",
            "Multi-Pin %": "MP %",
            "Strikes": "STR",
            "Strike Attempts": "STR Att",
            "Strike %": "STR %",
            "Baker Pts": "BKR Pts",
            "Baker Frames": "BKR Frm",
            "Baker Avg": "BKR Avg",
            "Average": "AVG",
        }
    )

    formatters: dict[str, str] = {}
    for column in ("SP %", "MP %", "STR %"):
        if column in view.columns:
            formatters[column] = "{:.2%}"
    if "BKR Avg" in view.columns:
        formatters["BKR Avg"] = "{:.1f}"
    if "AVG" in view.columns:
        formatters["AVG"] = "{:.2f}"
    for column in view.columns:
        if re.fullmatch(r"Game \d+", str(column)):
            formatters[str(column)] = "{:.0f}"

    def percentage_color(value: object, thresholds: tuple[float, float]) -> str:
        if value is None or pd.isna(value):
            return ""
        green_threshold, yellow_threshold = thresholds
        if float(value) >= green_threshold:
            return "background-color: #C6EFCE"
        if float(value) >= yellow_threshold:
            return "background-color: #FFEB9C"
        return "background-color: #FFC7CE"

    styled = view.style.format(formatters, na_rep="")
    if "SP %" in view.columns:
        styled = styled.map(
            lambda value: percentage_color(value, single_thresholds),
            subset=["SP %"],
        )
    if "MP %" in view.columns:
        styled = styled.map(
            lambda value: percentage_color(value, MULTI_PIN_THRESHOLDS),
            subset=["MP %"],
        )
    if "Name" in view.columns:
        styled = styled.apply(
            lambda row: ["font-weight: bold"] * len(row)
            if row["Name"] == "Total"
            else [""] * len(row),
            axis=1,
        )

    column_config: dict[str, object] = {
        str(column): st.column_config.Column(str(column), width="small")
        for column in view.columns
    }
    if "Name" in view.columns:
        column_config["Name"] = st.column_config.TextColumn("Name", width="medium", pinned=True)
    return styled, column_config


def _render_team_baker_detail(detail: pd.DataFrame) -> None:
    team_detail, baker_detail = split_team_baker_detail(detail)
    team_column, baker_column = st.columns(2)
    with team_column:
        st.caption("Team")
        st.markdown(
            _render_table_html(
                team_detail,
                color_make_pct=False,
                color_statistic=True,
                compact=True,
            ),
            unsafe_allow_html=True,
        )
    with baker_column:
        st.caption("Baker")
        st.markdown(
            _render_table_html(
                baker_detail,
                color_make_pct=False,
                color_statistic=True,
                compact=True,
            ),
            unsafe_allow_html=True,
        )


@st.cache_data(show_spinner=False)
def _build_event_pdf_zip(
    team: str,
    event: str,
    source_files: tuple[str, ...],
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None,
    baker_frames: pd.DataFrame | None,
    team_games: pd.DataFrame | None,
) -> bytes:
    pdf_style_version = "event-filename-v3"
    del pdf_style_version
    frame_details: dict[str, pd.DataFrame] = {}
    for athlete in pdf_athlete_names(records, frame_attempts, baker_frames, team_games):
        detail = _build_combined_ball_by_ball_detail(
            list(source_files),
            team,
            selected_event=event,
            selected_athlete=athlete,
            include_bowler_metadata=True,
        )
        detail = add_statistic_classification(detail, records, frame_attempts, athlete)
        if "Game ID" in detail.columns:
            detail = detail.drop(columns=["Game ID"])
        if "Game Type" in detail.columns and "Frame" in detail.columns:
            display_columns = detail.columns.tolist()
            display_columns.remove("Game Type")
            display_columns.insert(display_columns.index("Frame"), "Game Type")
            detail = detail[display_columns]
        frame_details[athlete] = detail

    return build_athlete_pdf_reports_zip(
        team,
        records,
        frame_attempts,
        baker_frames,
        team_games,
        event=event,
        frame_details=frame_details,
    )


@st.cache_data(show_spinner=False)
def _build_individual_pdf(
    team: str,
    event: str,
    athlete: str,
    source_files: tuple[str, ...],
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None,
    baker_frames: pd.DataFrame | None,
    team_games: pd.DataFrame | None,
) -> bytes:
    report_event = None if event == "All Events" else event
    frame_detail = None
    if report_event:
        frame_detail = _build_combined_ball_by_ball_detail(
            list(source_files),
            team,
            selected_event=report_event,
            selected_athlete=athlete,
            include_bowler_metadata=True,
        )
        frame_detail = add_statistic_classification(
            frame_detail,
            records,
            frame_attempts,
            athlete,
        )
        if "Game ID" in frame_detail.columns:
            frame_detail = frame_detail.drop(columns=["Game ID"])
        if "Game Type" in frame_detail.columns and "Frame" in frame_detail.columns:
            display_columns = frame_detail.columns.tolist()
            display_columns.remove("Game Type")
            display_columns.insert(display_columns.index("Frame"), "Game Type")
            frame_detail = frame_detail[display_columns]

    return build_athlete_report_pdf(
        athlete,
        team,
        records,
        frame_attempts,
        baker_frames,
        team_games,
        event=report_event,
        frame_detail=frame_detail,
    )


records = st.session_state.get("records")
if records is not None:
    statistics_header_slot = st.empty()
    selected_single_thresholds = single_pin_thresholds(
        st.session_state.get("team_scope", team_scope)
    )

    frame_attempts = st.session_state.get("frame_attempts")
    baker_frames = st.session_state.get("baker_frames")
    team_games = st.session_state.get("team_games")
    event_sources = []
    for frame in (records, frame_attempts, baker_frames, team_games):
        if frame is not None and not frame.empty:
            event_sources.append(frame[["event", "game_date"]])
    if event_sources:
        event_table = pd.concat(event_sources, ignore_index=True).dropna(subset=["event"]).copy()
        event_table["event"] = event_table["event"].astype(str)
        event_table["__date"] = pd.to_datetime(event_table["game_date"], errors="coerce")
        event_order = (
            event_table.groupby("event", dropna=False)["__date"]
            .min()
            .reset_index()
            .sort_values(["__date", "event"], ascending=[True, True], na_position="last")
        )
        event_values = event_order["event"].tolist()
    else:
        event_values = []
    athlete_names: set[str] = set(records["athlete"].dropna().astype(str)) if not records.empty else set()
    for frame in (frame_attempts, baker_frames, team_games):
        if frame is not None and not frame.empty:
            athlete_names.update(frame["athlete"].dropna().astype(str))
    athlete_values_all = sorted(athlete_names)
    athlete_options = ["All Athletes"] + athlete_values_all
    previous_event_filter = st.session_state.pop("event_filter", None)
    if "event_filter_multi" not in st.session_state:
        st.session_state["event_filter_multi"] = (
            [previous_event_filter]
            if previous_event_filter in event_values
            else []
        )
    else:
        st.session_state["event_filter_multi"] = [
            event
            for event in st.session_state["event_filter_multi"]
            if event in event_values
        ]
    if st.session_state.get("athlete_filter") not in athlete_options:
        st.session_state["athlete_filter"] = "All Athletes"

    col_team, col_event, col_squad, col_athlete = st.columns(4)
    with col_team:
        st.selectbox("Men/Women filter", team_options, key="team_data_scope")
    with col_event:
        selected_events = st.multiselect(
            "Event filter",
            event_values,
            key="event_filter_multi",
            placeholder="All Events",
            help="Leave empty for all events, or select one or more events.",
        )
    with col_squad:
        st.selectbox("Team filter", squad_options, key="squad_data_scope")
    with col_athlete:
        selected_athlete = st.selectbox("Athlete preview", athlete_options, key="athlete_filter")
    athlete_filter = None if selected_athlete == "All Athletes" else selected_athlete
    all_events_selected = not selected_events
    single_event_selected = len(selected_events) == 1
    selected_event = selected_events[0] if single_event_selected else "All Events"

    records_event = records.copy()
    frame_attempts_event = frame_attempts.copy() if frame_attempts is not None else None
    baker_frames_event = baker_frames.copy() if baker_frames is not None else None
    team_games_event = team_games.copy() if team_games is not None else None
    if not all_events_selected:
        records_event = records_event[records_event["event"].isin(selected_events)].copy()
        if frame_attempts_event is not None:
            frame_attempts_event = frame_attempts_event[
                frame_attempts_event["event"].isin(selected_events)
            ].copy()
        if baker_frames_event is not None:
            baker_frames_event = baker_frames_event[
                baker_frames_event["event"].isin(selected_events)
            ].copy()
        if team_games_event is not None:
            team_games_event = team_games_event[
                team_games_event["event"].isin(selected_events)
            ].copy()

    if all_events_selected:
        pdf_zip_bytes = st.session_state.get("athlete_pdf_zip_bytes")
        download_help = "Download athlete PDF reports for all events"
    elif single_event_selected:
        pdf_zip_bytes = _build_event_pdf_zip(
            team_scope,
            selected_event,
            tuple(st.session_state.get("source_files", [])),
            records_event,
            frame_attempts_event,
            baker_frames_event,
            team_games_event,
        )
        download_help = f"Download athlete PDF reports for {selected_event}"
    else:
        pdf_zip_bytes = None
        download_help = ""

    group_by_event_for_athlete = athlete_filter is not None
    summary_preview = _build_summary_preview(
        records_event,
        frame_attempts_event,
        baker_frames_event,
        team_games_event,
        athlete_filter=athlete_filter,
        group_by_event_for_athlete=group_by_event_for_athlete,
        include_total_row=True,
    )
    if athlete_filter is None:
        summary_preview = append_event_total_row(summary_preview, team_games_event)
    if single_event_selected:
        summary_preview = add_team_game_score_columns(
            summary_preview,
            team_games_event,
            athlete_filter=athlete_filter,
        )

    with statistics_header_slot.container():
        statistics_title_col, statistics_download_col = st.columns([3, 1])
        with statistics_title_col:
            st.subheader("Bowler Statistics")
        with statistics_download_col:
            individual_pdf_bytes = None
            if athlete_filter is not None and (all_events_selected or single_event_selected):
                individual_pdf_bytes = _build_individual_pdf(
                    team_scope,
                    selected_event,
                    athlete_filter,
                    tuple(st.session_state.get("source_files", [])),
                    records_event,
                    frame_attempts_event,
                    baker_frames_event,
                    team_games_event,
                )
            individual_col, excel_col, bulk_col = st.columns(3)
            with individual_col:
                if individual_pdf_bytes is not None:
                    report_event = selected_event if single_event_selected else None
                    st.download_button(
                        label=f"Download PDF for {athlete_filter}",
                        data=individual_pdf_bytes,
                        file_name=athlete_report_filename(athlete_filter, report_event),
                        mime="application/pdf",
                        icon=":material/picture_as_pdf:",
                        help=f"Download the PDF report for {athlete_filter}",
                    )
            with excel_col:
                if single_event_selected:
                    st.download_button(
                        label=f"Download {selected_event} Overall Summary as Excel",
                        data=build_summary_workbook(summary_preview, team_scope, selected_event),
                        file_name=_summary_excel_filename(team_scope, selected_event),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        icon=":material/table_view:",
                        help=f"Download the displayed Overall Summary for {selected_event}",
                    )
            with bulk_col:
                if pdf_zip_bytes is not None:
                    st.download_button(
                        label=f"Download Athlete PDF Reports - {selected_event}",
                        data=pdf_zip_bytes,
                        file_name=_download_filename(team_scope, selected_event),
                        mime="application/zip",
                        icon=":material/folder_zip:",
                        help=download_help,
                    )

    if athlete_filter is not None:
        st.caption(f"Summary rows are grouped by event for {athlete_filter}.")
    st.markdown("**Overall Summary**")
    if summary_preview.empty:
        st.info("No summary data available.")
    else:
        summary_grid, summary_column_config = _build_summary_grid(
            summary_preview,
            selected_single_thresholds,
        )
        st.dataframe(
            summary_grid,
            width="stretch",
            height=40 + (35 * len(summary_preview)),
            hide_index=True,
            column_config=summary_column_config,
            key="overall_summary_table",
            row_height=35,
        )

    if not records_event.empty:
        single_event_mode = single_event_selected
        single_attempt_detail = pd.DataFrame()
        multi_attempt_detail = pd.DataFrame()
        strike_attempt_detail = pd.DataFrame()
        if athlete_filter is not None and SHOW_ATTEMPT_DETAIL:
            strike_attempt_detail = _build_strike_attempt_detail(frame_attempts_event, athlete_filter)
            all_frame_detail = _build_combined_ball_by_ball_detail(
                st.session_state.get("source_files", []),
                st.session_state.get("team_scope", ALL_TEAMS),
                selected_event="All Events",
                selected_athlete="All Athletes",
            )
            single_attempt_detail = _build_spare_attempt_detail(
                records_event,
                all_frame_detail,
                athlete_filter,
                is_single=True,
            )
            multi_attempt_detail = _build_spare_attempt_detail(
                records_event,
                all_frame_detail,
                athlete_filter,
                is_single=False,
            )

        if single_event_mode:
            single_all = _build_leave_preview_all(records_event, athlete_filter, is_single=True)
            multi_all = _build_leave_preview_all(records_event, athlete_filter, is_single=False)
        else:
            single_common, single_rare = _build_leave_preview(records_event, athlete_filter, is_single=True)
            multi_common, multi_rare = _build_leave_preview(records_event, athlete_filter, is_single=False)

        if SHOW_ATTEMPT_DETAIL:
            tab_single, tab_multi, tab_strike = st.tabs(["Single Pin", "Multi Pin", "Strike"])
            with tab_single:
                if single_event_mode:
                    st.caption("All combinations (single event)")
                    st.markdown(
                        _render_table_html(
                            single_all,
                            color_make_pct=True,
                            make_pct_thresholds=selected_single_thresholds,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption(_common_combinations_caption(single_common, single_rare))
                    st.markdown(
                        _render_table_html(
                            single_common,
                            color_make_pct=True,
                            make_pct_thresholds=selected_single_thresholds,
                        ),
                        unsafe_allow_html=True,
                    )
                    if not single_rare.empty:
                        with st.expander(
                            _rare_combinations_label("Single Pin", single_common, single_rare),
                            expanded=False,
                        ):
                            st.markdown(
                                _render_table_html(
                                    single_rare,
                                    color_make_pct=True,
                                    make_pct_thresholds=selected_single_thresholds,
                                ),
                                unsafe_allow_html=True,
                            )
                if athlete_filter is not None:
                    with st.expander("View single-pin attempt detail"):
                        st.markdown(_render_table_html(single_attempt_detail), unsafe_allow_html=True)
            with tab_multi:
                if single_event_mode:
                    st.caption("All combinations (single event)")
                    st.markdown(
                        _render_table_html(
                            multi_all,
                            color_make_pct=True,
                            make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption(_common_combinations_caption(multi_common, multi_rare))
                    st.markdown(
                        _render_table_html(
                            multi_common,
                            color_make_pct=True,
                            make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                        ),
                        unsafe_allow_html=True,
                    )
                    if not multi_rare.empty:
                        with st.expander(
                            _rare_combinations_label("Multi Pin", multi_common, multi_rare),
                            expanded=False,
                        ):
                            st.markdown(
                                _render_table_html(
                                    multi_rare,
                                    color_make_pct=True,
                                    make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                                ),
                                unsafe_allow_html=True,
                            )
                if athlete_filter is not None:
                    with st.expander("View multi-pin attempt detail"):
                        st.caption("Only attempts counted by the Option 1 split rule are shown.")
                        st.markdown(_render_table_html(multi_attempt_detail), unsafe_allow_html=True)
            with tab_strike:
                if athlete_filter is None:
                    st.caption("Select an athlete to view strike attempt detail.")
                else:
                    with st.expander("View strike attempt detail"):
                        st.caption(
                            "Ball 3 counts as a strike attempt only after two strikes or a spare, and only when Ball 3 is a strike."
                        )
                        st.markdown(_render_table_html(strike_attempt_detail), unsafe_allow_html=True)
        else:
            if single_event_mode:
                single_col, multi_col = st.columns(2)
                rare_col = None
            elif single_rare.empty and multi_rare.empty:
                single_col, multi_col = st.columns(2)
                rare_col = None
            else:
                single_col, multi_col, rare_col = st.columns(3)

            with single_col:
                st.markdown("**Single Pin**")
                if single_event_mode:
                    st.caption("All combinations (single event)")
                    st.markdown(
                        _render_table_html(
                            single_all,
                            color_make_pct=True,
                            make_pct_thresholds=selected_single_thresholds,
                            compact=True,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption(_common_combinations_caption(single_common, single_rare))
                    st.markdown(
                        _render_table_html(
                            single_common,
                            color_make_pct=True,
                            make_pct_thresholds=selected_single_thresholds,
                            compact=True,
                        ),
                        unsafe_allow_html=True,
                    )
            with multi_col:
                st.markdown("**Multi Pin**")
                if single_event_mode:
                    st.caption("All combinations (single event)")
                    st.markdown(
                        _render_table_html(
                            multi_all,
                            color_make_pct=True,
                            make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                            compact=True,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption(_common_combinations_caption(multi_common, multi_rare))
                    st.markdown(
                        _render_table_html(
                            multi_common,
                            color_make_pct=True,
                            make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                            compact=True,
                        ),
                        unsafe_allow_html=True,
                    )

            if rare_col is not None:
                with rare_col:
                    st.markdown("**Rare Combinations**")
                    if not single_rare.empty:
                        with st.expander(
                            _rare_combinations_label("Single Pin", single_common, single_rare),
                            expanded=False,
                        ):
                            st.markdown(
                                _render_table_html(
                                    single_rare,
                                    color_make_pct=True,
                                    make_pct_thresholds=selected_single_thresholds,
                                    compact=True,
                                ),
                                unsafe_allow_html=True,
                            )
                    if not multi_rare.empty:
                        with st.expander(
                            _rare_combinations_label("Multi Pin", multi_common, multi_rare),
                            expanded=False,
                        ):
                            st.markdown(
                                _render_table_html(
                                    multi_rare,
                                    color_make_pct=True,
                                    make_pct_thresholds=MULTI_PIN_THRESHOLDS,
                                    compact=True,
                                ),
                                unsafe_allow_html=True,
                            )

        if single_event_selected and athlete_filter is not None:
            st.markdown("**Ball-by-Ball Detail**")
            ball_detail = _build_combined_ball_by_ball_detail(
                st.session_state.get("source_files", []),
                st.session_state.get("team_scope", ALL_TEAMS),
                selected_event=selected_event,
                selected_athlete=athlete_filter,
                include_bowler_metadata=True,
            )
            ball_detail = add_statistic_classification(
                ball_detail,
                records_event,
                frame_attempts_event,
                athlete_filter,
            )
            if "Game ID" in ball_detail.columns:
                ball_detail = ball_detail.drop(columns=["Game ID"])
            if "Game Type" in ball_detail.columns and "Frame" in ball_detail.columns:
                display_columns = ball_detail.columns.tolist()
                display_columns.remove("Game Type")
                display_columns.insert(display_columns.index("Frame"), "Game Type")
                ball_detail = ball_detail[display_columns]
            _render_team_baker_detail(ball_detail)
    else:
        st.info("No records found for the selected event.")
