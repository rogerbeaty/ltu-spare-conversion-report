from __future__ import annotations

from pathlib import Path

import pytest

from src.storage import (
    delete_all_uploaded_csvs,
    delete_uploaded_csv,
    list_uploaded_csvs,
    save_uploaded_csv,
    save_uploaded_csv_batch,
    uploaded_csv_label,
)


def test_save_list_and_delete_uploaded_csv(tmp_path: Path) -> None:
    first = save_uploaded_csv(tmp_path, "../WHAC 1.csv", b"a,b\n1,2\n")
    duplicate = save_uploaded_csv(tmp_path, "renamed.csv", b"a,b\n1,2\n")

    assert first == duplicate
    assert first.parent == tmp_path
    assert first.name.endswith("__WHAC 1.csv")
    assert uploaded_csv_label(first).startswith("WHAC 1.csv (")
    assert list_uploaded_csvs(tmp_path) == [first]

    assert delete_uploaded_csv(tmp_path, first.name) is True
    assert delete_uploaded_csv(tmp_path, first.name) is False
    assert list_uploaded_csvs(tmp_path) == []


def test_delete_rejects_paths_outside_upload_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        delete_uploaded_csv(tmp_path, "../other.csv")


def test_corrected_upload_replaces_prior_file_with_same_name(tmp_path: Path) -> None:
    original = save_uploaded_csv(tmp_path, "WHAC 1.csv", b"name\nMatt Schepis\n")
    corrected = save_uploaded_csv(tmp_path, "WHAC 1.csv", b"name\nMatthew Schepis\n")

    assert original != corrected
    assert not original.exists()
    assert corrected.exists()
    assert corrected.read_bytes() == b"name\nMatthew Schepis\n"
    assert list_uploaded_csvs(tmp_path) == [corrected]


def test_save_uploaded_csv_batch_saves_every_selected_file(tmp_path: Path) -> None:
    stored = save_uploaded_csv_batch(
        tmp_path,
        [
            ("LTU-Mens-WHAC-1.csv", b"event,team\nWHAC 1,Men\n"),
            ("LTU-Womens-WHAC-1.csv", b"event,team\nWHAC 1,Women\n"),
        ],
    )

    assert len(stored) == 2
    assert {uploaded_csv_label(path).split(" (")[0] for path in stored} == {
        "LTU-Mens-WHAC-1.csv",
        "LTU-Womens-WHAC-1.csv",
    }
    assert set(list_uploaded_csvs(tmp_path)) == set(stored)


def test_delete_all_uploaded_csvs_removes_every_saved_upload(tmp_path: Path) -> None:
    save_uploaded_csv_batch(
        tmp_path,
        [
            ("first.csv", b"event\nFirst\n"),
            ("second.csv", b"event\nSecond\n"),
        ],
    )
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("not an upload", encoding="utf-8")

    assert delete_all_uploaded_csvs(tmp_path) == 2
    assert list_uploaded_csvs(tmp_path) == []
    assert unrelated.exists()
    assert delete_all_uploaded_csvs(tmp_path) == 0
