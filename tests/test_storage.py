from __future__ import annotations

from pathlib import Path

import pytest

from src.storage import delete_uploaded_csv, list_uploaded_csvs, save_uploaded_csv, uploaded_csv_label


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
