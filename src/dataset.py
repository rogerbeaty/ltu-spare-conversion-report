from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

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
