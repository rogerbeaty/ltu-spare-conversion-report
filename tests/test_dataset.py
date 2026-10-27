from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.auth import ADMIN, AppDatabase
from src.dataset import (
    ALL_TEAMS,
    JV,
    MEN,
    VARSITY,
    WOMEN,
    classify_squad,
    classify_squad_from_upload,
    classify_team_from_filename,
    combine_saved_datasets,
    ensure_processed_datasets,
    load_processed_datasets,
    processed_upload_metadata,
)


def _write_team_csv(path: Path, athlete: str, event: str) -> None:
    pd.DataFrame(
        [
            {
                "Event": event,
                "GameType": "Team",
                "Frame1Ball1Bowler": athlete,
                "Frame1Ball1": 9,
                "Frame1Ball2": 1,
                "Frame1Ball1Pins": "10",
            }
        ]
    ).to_csv(path, index=False)


def test_classify_team_from_filename() -> None:
    assert classify_team_from_filename("hash__LTU-Mens-varsity.csv") == MEN
    assert classify_team_from_filename("hash__LTU-Womens-varsity.csv") == WOMEN
    assert classify_team_from_filename("unknown.csv") == "Unclassified"


def test_classify_squad_uses_jv_marker_in_team_name() -> None:
    assert classify_squad(["2026 AHBA 2 JV"]) == JV
    assert classify_squad(["2026 AHBA 2 (JV 2 men)"]) == JV
    assert classify_squad(["Women's Varsity"]) == VARSITY
    assert classify_squad(["2026 Detroit Tier 1"]) == VARSITY
    assert classify_squad(["2026 Detroit MCC"]) == VARSITY
    assert classify_squad([]) == VARSITY


def test_classify_squad_from_frame_column_team_name(tmp_path: Path) -> None:
    upload = tmp_path / "hash__export.csv"
    pd.DataFrame(
        [{"Event": "Event 1", "Team": "Women's JV", "Frame1Ball1": 10}]
    ).to_csv(upload, index=False)

    assert classify_squad_from_upload(upload) == JV


def test_combine_saved_datasets_and_make_game_ids_unique(tmp_path: Path) -> None:
    men = tmp_path / "aaa__LTU-Mens-WHAC.csv"
    women = tmp_path / "bbb__LTU-Womens-WHAC.csv"
    _write_team_csv(men, "Alex", "WHAC 1")
    _write_team_csv(women, "Alex", "WHAC 1")

    combined = combine_saved_datasets([men, women], ALL_TEAMS)

    assert set(combined.records["athlete"]) == {"Alex"}
    assert combined.records["game_id"].nunique() == 2
    assert len(combined.source_files) == 2

    men_only = combine_saved_datasets([men, women], MEN)
    assert set(men_only.records["athlete"]) == {"Alex"}
    assert set(men_only.records["team_group"]) == {MEN}


def test_processed_dataset_is_persisted_and_replaced(tmp_path: Path) -> None:
    database = AppDatabase(f"sqlite:///{tmp_path / 'processed.db'}")
    database.initialize()
    admin = database.create_user(
        None,
        "Admin",
        "admin@example.test",
        ADMIN,
        password="AdminPassword1!",
        must_change_password=False,
    )

    first_csv = tmp_path / "first.csv"
    _write_team_csv(first_csv, "Matt", "Event 1")
    first = database.save_uploads(
        admin,
        [("LTU-Mens-Varsity.csv", first_csv.read_bytes())],
    )[0]

    assert ensure_processed_datasets(database, "test-v1") == 1
    assert ensure_processed_datasets(database, "test-v1") == 0
    combined = load_processed_datasets(database, [first.stored_name])
    assert combined.records["athlete"].tolist() == ["Matt"]
    assert combined.records["made"].tolist() == [True]
    assert set(combined.records["team_group"]) == {MEN}
    assert processed_upload_metadata(database)[first.stored_name] == (MEN, VARSITY)

    replacement_csv = tmp_path / "replacement.csv"
    _write_team_csv(replacement_csv, "Matthew", "Event 1")
    replacement = database.save_uploads(
        admin,
        [("LTU-Mens-Varsity.csv", replacement_csv.read_bytes())],
    )[0]

    assert replacement.id == first.id
    assert ensure_processed_datasets(database, "test-v1") == 1
    replaced = load_processed_datasets(database, [replacement.stored_name])
    assert replaced.records["athlete"].tolist() == ["Matthew"]
    assert processed_upload_metadata(database).keys() == {replacement.stored_name}

    assert database.delete_upload(admin, replacement.stored_name)
    assert processed_upload_metadata(database) == {}
