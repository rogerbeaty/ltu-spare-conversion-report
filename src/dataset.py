from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from src.auth import (
    AppDatabase,
    BakerFrameRow,
    FrameAttemptRow,
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
    source_files: list[str]


PROCESSED_MODELS = (SpareOpportunityRow, FrameAttemptRow, BakerFrameRow, TeamGameRow)
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
                session.delete(existing)
                session.flush()

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
            session.commit()
    return len(uploads)


def processed_upload_metadata(database: AppDatabase) -> dict[str, tuple[str, str]]:
    with Session(database.engine) as session:
        rows = session.execute(
            select(UploadRow.stored_name, ProcessedUploadRow.team_group, ProcessedUploadRow.squad_group)
            .join(ProcessedUploadRow, ProcessedUploadRow.upload_id == UploadRow.id)
        ).all()
    return {stored_name: (team, squad) for stored_name, team, squad in rows}


def load_processed_datasets(database: AppDatabase, stored_names: list[str]) -> CombinedDataset:
    if not stored_names:
        return CombinedDataset(*(pd.DataFrame() for _ in range(4)), source_files=[])

    frame_rows: dict[type, list[dict[str, Any]]] = {model: [] for model in PROCESSED_MODELS}
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
        for model in PROCESSED_MODELS:
            rows = session.scalars(
                select(model)
                .where(model.dataset_id.in_(dataset_ids))
                .order_by(model.dataset_id, model.id)
            )
            for row in rows:
                stored_name, source_key, team_group, squad_group = dataset_details[row.dataset_id]
                item = {column: getattr(row, column) for column in FRAME_COLUMNS[model]}
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

    return CombinedDataset(
        records=_concat_or_empty(record_frames),
        frame_attempts=_concat_or_empty(attempt_frames),
        baker_frames=_concat_or_empty(baker_frames),
        team_games=_concat_or_empty(team_game_frames),
        source_files=source_files,
    )


def _concat_or_empty(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
