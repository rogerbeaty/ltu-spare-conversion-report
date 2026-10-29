from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session

from src.auth import (
    AppDatabase,
    AthleteAggregateRow,
    BakerFrameRow,
    FrameAttemptRow,
    PinLeaveAggregateRow,
    ProcessedUploadRow,
    SpareOpportunityRow,
    TeamGameRow,
    UploadRow,
)
from src.parser import extract_team_names, parse_bowlsheet_csv

ALL_TEAMS = "All Teams"
MEN = "Men"
WOMEN = "Women"
UNCLASSIFIED = "Unclassified"
ALL_SQUADS = "All Teams"
VARSITY = "Varsity"
JV = "JV"


@dataclass
class CombinedDataset:
    records: pd.DataFrame
    frame_attempts: pd.DataFrame
    baker_frames: pd.DataFrame
    team_games: pd.DataFrame
    summary_rows: pd.DataFrame
    leave_rows: pd.DataFrame
    source_files: list[str]


RAW_MODELS = (SpareOpportunityRow, FrameAttemptRow, BakerFrameRow, TeamGameRow)
AGGREGATE_MODELS = (AthleteAggregateRow, PinLeaveAggregateRow)
PROCESSED_MODELS = RAW_MODELS + AGGREGATE_MODELS
DISPLAY_MODELS = (TeamGameRow,) + AGGREGATE_MODELS
FRAME_COLUMNS = {
    SpareOpportunityRow: [
        "game_id", "event", "game_date", "frame", "athlete", "pin_leave",
        "is_single", "split", "made", "count_attempt",
    ],
    FrameAttemptRow: [
        "game_id", "event", "game_date", "game_type", "frame", "ball", "pins",
        "athlete", "is_strike",
    ],
    BakerFrameRow: [
        "game_id", "event", "game_date", "game_type", "frame", "athlete", "points",
    ],
    TeamGameRow: [
        "game_id", "event", "game_date", "game_type", "athlete", "score",
    ],
    AthleteAggregateRow: [
        "event", "event_date", "athlete", "single_made", "single_attempts",
        "multi_made", "multi_attempts", "strikes", "strike_attempts",
        "baker_points", "baker_frames", "team_score_total", "team_games",
    ],
    PinLeaveAggregateRow: [
        "event", "athlete", "pin_leave", "is_single", "made", "attempts",
    ],
}


def classify_team_from_filename(filename: str) -> str:
    original_name = filename.partition("__")[2] or filename
    normalized = "".join(ch.lower() if ch.isalpha() else " " for ch in original_name)
    tokens = normalized.split()

    if any(token in {"women", "womens", "female", "w"} for token in tokens):
        return WOMEN
    if any(token in {"men", "mens", "male", "m"} for token in tokens):
        return MEN
    return UNCLASSIFIED


def classify_squad(team_names: list[str]) -> str:
    return JV if any("jv" in team_name.casefold() for team_name in team_names) else VARSITY


def classify_squad_from_upload(path: Path) -> str:
    return classify_squad(extract_team_names(path.read_bytes()))


def ensure_processed_datasets(database: AppDatabase, parser_version: str) -> int:
    """Parse only uploads that are new or were built by an older parser."""
    with Session(database.engine) as session:
        uploads = session.scalars(
            select(UploadRow)
            .outerjoin(ProcessedUploadRow, ProcessedUploadRow.upload_id == UploadRow.id)
            .where(
                or_(
                    ProcessedUploadRow.id.is_(None),
                    ProcessedUploadRow.parser_version != parser_version,
                    ProcessedUploadRow.source_digest != UploadRow.digest,
                    ~exists().where(
                        AthleteAggregateRow.dataset_id == ProcessedUploadRow.id
                    ),
                )
            )
            .order_by(UploadRow.id)
        ).all()

    for upload in uploads:
        parsed = parse_bowlsheet_csv(upload.content)
        team = classify_team_from_filename(upload.stored_name)
        squad = classify_squad(extract_team_names(upload.content))
        with Session(database.engine) as session:
            existing = session.scalar(
                select(ProcessedUploadRow).where(ProcessedUploadRow.upload_id == upload.id)
            )
            if existing is not None:
                for model in PROCESSED_MODELS:
                    session.execute(delete(model).where(model.dataset_id == existing.id))
                dataset = existing
                dataset.parser_version = parser_version
                dataset.source_digest = upload.digest
                dataset.team_group = team
                dataset.squad_group = squad
                dataset.detected_format = parsed.detected_format
                dataset.games_processed = parsed.games_processed
                dataset.processed_at = datetime.now(timezone.utc)
            else:
                dataset = ProcessedUploadRow(
                    upload_id=upload.id,
                    parser_version=parser_version,
                    source_digest=upload.digest,
                    team_group=team,
                    squad_group=squad,
                    detected_format=parsed.detected_format,
                    games_processed=parsed.games_processed,
                    processed_at=datetime.now(timezone.utc),
                )
                session.add(dataset)
            session.flush()

            for frame, model in (
                (parsed.records, SpareOpportunityRow),
                (parsed.frame_attempts, FrameAttemptRow),
                (parsed.baker_frames, BakerFrameRow),
                (parsed.team_games, TeamGameRow),
            ):
                mappings = _frame_mappings(frame, FRAME_COLUMNS[model], dataset.id)
                if mappings:
                    session.bulk_insert_mappings(model, mappings)

            summary_rows, leave_rows = build_report_aggregates(
                parsed.records,
                parsed.frame_attempts,
                parsed.baker_frames,
                parsed.team_games,
            )
            for frame, model in (
                (summary_rows, AthleteAggregateRow),
                (leave_rows, PinLeaveAggregateRow),
            ):
                mappings = _frame_mappings(frame, FRAME_COLUMNS[model], dataset.id)
                if mappings:
                    session.bulk_insert_mappings(model, mappings)
            session.commit()
    return len(uploads)


def processed_upload_metadata(database: AppDatabase) -> dict[str, tuple[str, str]]:
    with Session(database.engine) as session:
        rows = session.execute(
            select(UploadRow.stored_name, ProcessedUploadRow.team_group, ProcessedUploadRow.squad_group)
            .join(ProcessedUploadRow, ProcessedUploadRow.upload_id == UploadRow.id)
        ).all()
    return {stored_name: (team, squad) for stored_name, team, squad in rows}


def load_processed_datasets(
    database: AppDatabase,
    stored_names: list[str],
    *,
    include_raw: bool = True,
    include_aggregates: bool = True,
    events: tuple[str, ...] = (),
    athletes: tuple[str, ...] = (),
) -> CombinedDataset:
    """Load display aggregates, optionally including raw rows used by details/exports."""
    if not stored_names:
        return CombinedDataset(*(pd.DataFrame() for _ in range(6)), source_files=[])

    frame_rows: dict[type, list[dict[str, Any]]] = {model: [] for model in PROCESSED_MODELS}
    if include_raw:
        models = RAW_MODELS + (AGGREGATE_MODELS if include_aggregates else ())
    else:
        models = DISPLAY_MODELS
    source_files: list[str] = []
    with Session(database.engine) as session:
        datasets = session.execute(
            select(ProcessedUploadRow, UploadRow.stored_name)
            .join(UploadRow, UploadRow.id == ProcessedUploadRow.upload_id)
            .where(UploadRow.stored_name.in_(stored_names))
            .order_by(UploadRow.updated_at.desc())
        ).all()
        dataset_details: dict[int, tuple[str, str, str, str]] = {}
        for dataset, stored_name in datasets:
            source_files.append(stored_name)
            source_key = stored_name.partition("__")[0][:12]
            dataset_details[dataset.id] = (
                stored_name,
                source_key,
                dataset.team_group,
                dataset.squad_group,
            )

        dataset_ids = list(dataset_details)
        for model in models:
            query = select(model).where(model.dataset_id.in_(dataset_ids))
            if events:
                query = query.where(model.event.in_(events))
            if athletes:
                query = query.where(model.athlete.in_(athletes))
            rows = session.scalars(query.order_by(model.dataset_id, model.id))
            for row in rows:
                stored_name, source_key, team_group, squad_group = dataset_details[row.dataset_id]
                item = {column: getattr(row, column) for column in FRAME_COLUMNS[model]}
                if "game_id" in item:
                    item["game_id"] = f"{source_key}:{item['game_id']}"
                item["team_group"] = team_group
                item["squad_group"] = squad_group
                item["source_file"] = stored_name
                frame_rows[model].append(item)

    return CombinedDataset(
        records=_stored_frame(frame_rows[SpareOpportunityRow], SpareOpportunityRow),
        frame_attempts=_stored_frame(frame_rows[FrameAttemptRow], FrameAttemptRow),
        baker_frames=_stored_frame(frame_rows[BakerFrameRow], BakerFrameRow),
        team_games=_stored_frame(frame_rows[TeamGameRow], TeamGameRow),
        summary_rows=_stored_frame(frame_rows[AthleteAggregateRow], AthleteAggregateRow),
        leave_rows=_stored_frame(frame_rows[PinLeaveAggregateRow], PinLeaveAggregateRow),
        source_files=source_files,
    )


def _frame_mappings(frame: pd.DataFrame, columns: list[str], dataset_id: int) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        mapping = {"dataset_id": dataset_id}
        for column in columns:
            value = record.get(column)
            if pd.isna(value):
                value = "" if column in {"game_date", "game_type", "pin_leave"} else None
            elif hasattr(value, "item"):
                value = value.item()
            if column == "game_id" and value is not None:
                value = str(value)
            mapping[column] = value
        mappings.append(mapping)
    return mappings


def _stored_frame(rows: list[dict[str, Any]], model: type) -> pd.DataFrame:
    columns = FRAME_COLUMNS[model] + ["team_group", "squad_group", "source_file"]
    return pd.DataFrame(rows, columns=columns)


def build_report_aggregates(
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame,
    baker_frames: pd.DataFrame,
    team_games: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build additive report totals that can be combined without raw-delivery scans."""
    pairs: set[tuple[str, str]] = set()
    for frame in (records, frame_attempts, baker_frames, team_games):
        if frame.empty or not {"event", "athlete"}.issubset(frame.columns):
            continue
        pairs.update(
            (str(event), str(athlete))
            for event, athlete in frame[["event", "athlete"]].dropna().itertuples(index=False)
        )

    summary_data: list[dict[str, Any]] = []
    for event, athlete in sorted(pairs):
        spare = _athlete_event_rows(records, event, athlete)
        attempts = _athlete_event_rows(frame_attempts, event, athlete)
        baker = _athlete_event_rows(baker_frames, event, athlete)
        games = _athlete_event_rows(team_games, event, athlete)
        single = spare[spare["is_single"]] if not spare.empty else spare
        multi = spare[~spare["is_single"]] if not spare.empty else spare
        date_values: list[Any] = []
        for frame in (spare, attempts, baker, games):
            if not frame.empty and "game_date" in frame.columns:
                date_values.extend(frame["game_date"].tolist())
        summary_data.append(
            {
                "event": event,
                "event_date": _earliest_date(date_values),
                "athlete": athlete,
                "single_made": _made_count(single),
                "single_attempts": _attempt_count(single),
                "multi_made": _made_count(multi),
                "multi_attempts": _attempt_count(multi),
                "strikes": int(attempts["is_strike"].sum()) if not attempts.empty else 0,
                "strike_attempts": int(len(attempts)),
                "baker_points": int(baker["points"].sum()) if not baker.empty else 0,
                "baker_frames": int(len(baker)),
                "team_score_total": int(games["score"].sum()) if not games.empty else 0,
                "team_games": int(len(games)),
            }
        )

    leave_columns = ["event", "athlete", "pin_leave", "is_single", "made", "attempts"]
    if records.empty:
        leave_rows = pd.DataFrame(columns=leave_columns)
    else:
        leave_source = records.copy()
        leave_source["made_count"] = (
            leave_source["made"].astype(bool) & leave_source["count_attempt"].astype(bool)
        ).astype(int)
        leave_source["attempt_count"] = leave_source["count_attempt"].astype(int)
        leave_rows = (
            leave_source.groupby(
                ["event", "athlete", "pin_leave", "is_single"],
                dropna=False,
            )
            .agg(made=("made_count", "sum"), attempts=("attempt_count", "sum"))
            .reset_index()
        )
        leave_rows = leave_rows[leave_rows["attempts"] > 0].reset_index(drop=True)
    return pd.DataFrame(summary_data, columns=FRAME_COLUMNS[AthleteAggregateRow]), leave_rows


def _athlete_event_rows(frame: pd.DataFrame, event: str, athlete: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[(frame["event"].astype(str) == event) & (frame["athlete"].astype(str) == athlete)]


def _attempt_count(frame: pd.DataFrame) -> int:
    return int(frame["count_attempt"].sum()) if not frame.empty else 0


def _made_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int((frame["made"].astype(bool) & frame["count_attempt"].astype(bool)).sum())


def _earliest_date(values: list[Any]) -> str:
    if not values:
        return ""
    source = pd.Series(values, dtype="object")
    source = source[source.notna() & (source.astype(str).str.strip() != "")]
    if source.empty:
        return ""
    parsed = pd.to_datetime(source, errors="coerce", format="mixed")
    if parsed.notna().any():
        return str(source.loc[parsed.idxmin()])
    return str(source.iloc[0])


def combine_saved_datasets(paths: list[Path], team_scope: str) -> CombinedDataset:
    record_frames: list[pd.DataFrame] = []
    attempt_frames: list[pd.DataFrame] = []
    baker_frames: list[pd.DataFrame] = []
    team_game_frames: list[pd.DataFrame] = []
    source_files: list[str] = []

    for path in paths:
        team = classify_team_from_filename(path.name)
        if team_scope != ALL_TEAMS and team != team_scope:
            continue

        csv_bytes = path.read_bytes()
        parsed = parse_bowlsheet_csv(csv_bytes)
        squad = classify_squad(extract_team_names(csv_bytes))
        source_key = path.name.partition("__")[0][:12]
        source_files.append(path.name)

        for frame, destination in (
            (parsed.records, record_frames),
            (parsed.frame_attempts, attempt_frames),
            (parsed.baker_frames, baker_frames),
            (parsed.team_games, team_game_frames),
        ):
            prepared = frame.copy()
            prepared["team_group"] = team
            prepared["squad_group"] = squad
            prepared["source_file"] = path.name
            if "game_id" in prepared.columns:
                prepared["game_id"] = source_key + ":" + prepared["game_id"].astype(str)
            destination.append(prepared)

    records = _concat_or_empty(record_frames)
    frame_attempts = _concat_or_empty(attempt_frames)
    baker = _concat_or_empty(baker_frames)
    games = _concat_or_empty(team_game_frames)
    summary_rows, leave_rows = build_report_aggregates(records, frame_attempts, baker, games)
    return CombinedDataset(
        records=records,
        frame_attempts=frame_attempts,
        baker_frames=baker,
        team_games=games,
        summary_rows=summary_rows,
        leave_rows=leave_rows,
        source_files=source_files,
    )


def _concat_or_empty(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
