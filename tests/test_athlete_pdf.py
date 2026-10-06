from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd

from src.athlete_pdf import (
    athlete_report_filename,
    build_athlete_pdf_reports_zip,
    build_athlete_report_pdf,
)


def _record(athlete: str, *, is_single: bool, made: bool, leave: str) -> dict[str, object]:
    return {
        "athlete": athlete,
        "event": "2026 WHAC 1",
        "game_date": "2026-09-20",
        "is_single": is_single,
        "count_attempt": True,
        "made": made,
        "pin_leave": leave,
    }


def _record_for_event(
    athlete: str,
    event: str,
    *,
    is_single: bool,
    made: bool,
    leave: str,
) -> dict[str, object]:
    record = _record(athlete, is_single=is_single, made=made, leave=leave)
    record["event"] = event
    return record


def test_athlete_pdf_is_valid_pdf() -> None:
    records = pd.DataFrame(
        [
            _record("Alex", is_single=True, made=True, leave="10"),
            _record("Alex", is_single=False, made=False, leave="2-4-5"),
        ]
    )

    pdf = build_athlete_report_pdf("Alex", "Men", records)

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1_000


def test_athlete_report_filename_changes_only_for_event_exports() -> None:
    assert athlete_report_filename("Alex") == "Alex - LTU Spare Report.pdf"
    assert athlete_report_filename("Alex", "2026 WHAC 1") == "Alex - 2026 WHAC 1.pdf"


def test_athlete_pdf_zip_contains_one_pdf_per_athlete() -> None:
    records = pd.DataFrame(
        [
            _record("Alex", is_single=True, made=True, leave="10"),
            _record("Blake", is_single=False, made=False, leave="2-4-5"),
        ]
    )

    with ZipFile(BytesIO(build_athlete_pdf_reports_zip("Women", records))) as archive:
        assert archive.namelist() == [
            "Alex - LTU Spare Report.pdf",
            "Blake - LTU Spare Report.pdf",
        ]
        assert archive.read("Alex - LTU Spare Report.pdf").startswith(b"%PDF-")
        assert archive.read("Blake - LTU Spare Report.pdf").startswith(b"%PDF-")


def test_event_pdf_zip_contains_only_athletes_in_selected_event() -> None:
    records = pd.DataFrame(
        [
            _record_for_event(
                "Alex",
                "2026 WHAC 1",
                is_single=True,
                made=True,
                leave="10",
            ),
            _record_for_event(
                "Blake",
                "2026 Detroit MCC",
                is_single=False,
                made=False,
                leave="2-4-5",
            ),
        ]
    )

    event_zip = build_athlete_pdf_reports_zip("Women", records, event="2026 WHAC 1")

    with ZipFile(BytesIO(event_zip)) as archive:
        assert archive.namelist() == ["Alex - 2026 WHAC 1.pdf"]
        assert archive.read("Alex - 2026 WHAC 1.pdf").startswith(b"%PDF-")


def test_event_pdf_accepts_frame_by_frame_detail() -> None:
    records = pd.DataFrame([_record("Alex", is_single=True, made=True, leave="10")])
    frame_detail = pd.DataFrame(
        [
            {
                "Game Type": "Team",
                "Frame": 1,
                "Points": "",
                "Ball1": 9,
                "Ball2": 1,
                "Ball3": "",
                "Statistic": "Single Pin Made",
            }
        ]
    )

    pdf = build_athlete_report_pdf(
        "Alex",
        "Men",
        records,
        event="2026 WHAC 1",
        frame_detail=frame_detail,
    )

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1_000
