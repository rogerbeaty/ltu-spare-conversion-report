from __future__ import annotations

from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.thresholds import MULTI_PIN_THRESHOLDS, single_pin_thresholds
from src.detail import abbreviate_statistic_label, split_team_baker_detail


NAVY = colors.HexColor("#17365D")
LIGHT_BLUE = colors.HexColor("#DCE6F1")
BORDER = colors.HexColor("#C8D0DA")
TEXT = colors.HexColor("#17202A")
GREEN = colors.HexColor("#C6EFCE")
YELLOW = colors.HexColor("#FFEB9C")
RED = colors.HexColor("#FFC7CE")

STATISTIC_CHIP_COLORS = {
    "strike": (colors.HexColor("#70AD47"), colors.white),
    "single pin made": (colors.HexColor("#C6E0B4"), TEXT),
    "multi pin made": (colors.HexColor("#E2F0D9"), TEXT),
    "single pin missed": (colors.HexColor("#E60000"), colors.white),
    "multi pin missed": (colors.HexColor("#F4CCCC"), TEXT),
    "split": (colors.HexColor("#FFF2CC"), TEXT),
    "fill": (colors.HexColor("#E7E6E6"), TEXT),
}


class StatisticChips(Flowable):
    font_name = "Helvetica-Bold"
    font_size = 6.3
    horizontal_padding = 4
    vertical_padding = 2
    chip_gap = 3
    line_gap = 2

    def __init__(self, value: object):
        super().__init__()
        text = "" if value is None or pd.isna(value) else str(value).strip()
        self.labels = [label.strip() for label in text.split(" / ") if label.strip()]
        self.lines: list[list[tuple[str, float]]] = []

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        del available_height
        chip_height = self.font_size + (2 * self.vertical_padding)
        self.lines = []
        current_line: list[tuple[str, float]] = []
        current_width = 0.0
        for label in self.labels:
            display_label = abbreviate_statistic_label(label)
            chip_width = min(
                available_width,
                pdfmetrics.stringWidth(display_label, self.font_name, self.font_size)
                + (2 * self.horizontal_padding),
            )
            required_width = chip_width if not current_line else self.chip_gap + chip_width
            if current_line and current_width + required_width > available_width:
                self.lines.append(current_line)
                current_line = []
                current_width = 0.0
                required_width = chip_width
            current_line.append((label, chip_width))
            current_width += required_width
        if current_line:
            self.lines.append(current_line)
        height = 0.0
        if self.lines:
            height = len(self.lines) * chip_height + (len(self.lines) - 1) * self.line_gap
        self.width = available_width
        self.height = height
        return available_width, height

    def draw(self) -> None:
        chip_height = self.font_size + (2 * self.vertical_padding)
        y = self.height - chip_height
        self.canv.setFont(self.font_name, self.font_size)
        for line in self.lines:
            x = 0.0
            for label, chip_width in line:
                background, foreground = _statistic_chip_colors(label)
                display_label = abbreviate_statistic_label(label)
                self.canv.setFillColor(background)
                self.canv.roundRect(x, y, chip_width, chip_height, chip_height / 2, fill=1, stroke=0)
                self.canv.setFillColor(foreground)
                self.canv.drawString(
                    x + self.horizontal_padding,
                    y + self.vertical_padding + 0.4,
                    display_label,
                )
                x += chip_width + self.chip_gap
            y -= chip_height + self.line_gap


def build_athlete_pdf_reports_zip(
    team: str,
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None = None,
    baker_frames: pd.DataFrame | None = None,
    team_games: pd.DataFrame | None = None,
    event: str | None = None,
    frame_details: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    records = _for_event(records, event)
    frame_attempts = _for_event(frame_attempts, event)
    baker_frames = _for_event(baker_frames, event)
    team_games = _for_event(team_games, event)
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for athlete in athlete_names(records, frame_attempts, baker_frames, team_games):
            archive.writestr(
                athlete_report_filename(athlete, event),
                build_athlete_report_pdf(
                    athlete,
                    team,
                    records,
                    frame_attempts,
                    baker_frames,
                    team_games,
                    event=event,
                    frame_detail=(frame_details or {}).get(athlete),
                ),
            )
    return output.getvalue()


def athlete_report_filename(athlete: str, event: str | None = None) -> str:
    if event:
        return f"{_safe_filename(athlete)} - {_safe_filename(event)}.pdf"
    return f"{_safe_filename(athlete)} - LTU Spare Report.pdf"


def build_athlete_report_pdf(
    athlete: str,
    team: str,
    records: pd.DataFrame,
    frame_attempts: pd.DataFrame | None = None,
    baker_frames: pd.DataFrame | None = None,
    team_games: pd.DataFrame | None = None,
    event: str | None = None,
    frame_detail: pd.DataFrame | None = None,
) -> bytes:
    athlete_records = _for_athlete(_for_event(records, event), athlete)
    athlete_strikes = _for_athlete(_for_event(frame_attempts, event), athlete)
    athlete_baker = _for_athlete(_for_event(baker_frames, event), athlete)
    athlete_team_games = _for_athlete(_for_event(team_games, event), athlete)
    summary = _event_summary(
        athlete_records,
        athlete_strikes,
        athlete_baker,
        athlete_team_games,
        include_total=event is None,
    )
    single = _leave_summary(athlete_records, is_single=True)
    multi = _leave_summary(athlete_records, is_single=False)
    single_thresholds = single_pin_thresholds(team)

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.4 * inch,
        title=f"{athlete} - LTU Spare Conversion Report",
        author="LTU Spare Conversion Report",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=21,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#52606D"),
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=NAVY,
        spaceBefore=4,
        spaceAfter=5,
    )

    subtitle = f"LTU {escape(team)} Team"
    if event:
        subtitle += f" - {escape(event)}"
    subtitle += " - Spare Conversion Report"
    story = [
        Paragraph(escape(athlete), title_style),
        Paragraph(subtitle, subtitle_style),
        Paragraph("Event Summary", heading_style),
        _summary_pdf_table(summary, single_thresholds),
        Spacer(1, 0.12 * inch),
        _combinations_pdf_table(single, multi, single_thresholds),
    ]
    if frame_detail is not None and not frame_detail.empty:
        story.extend(
            [
                Spacer(1, 0.12 * inch),
                Paragraph("Frame-by-Frame Detail", heading_style),
                _frame_detail_pdf_table(frame_detail),
            ]
        )
    document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return output.getvalue()


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


def _summary_pdf_table(
    summary: pd.DataFrame,
    single_thresholds: tuple[float, float],
) -> Table:
    headers = list(summary.columns)
    data = [headers]
    for _, row in summary.iterrows():
        data.append([_format_value(header, row[header]) for header in headers])

    table = Table(
        data,
        colWidths=[1.35 * inch] + [0.46 * inch] * (len(headers) - 1),
        repeatRows=1,
        hAlign="LEFT",
    )
    style = _base_table_style(font_size=6.4)
    style.add("ALIGN", (1, 1), (-1, -1), "CENTER")
    style.add("ALIGN", (0, 1), (0, -1), "LEFT")
    for row_index, (_, row) in enumerate(summary.iterrows(), start=1):
        _add_percentage_fill(style, row_index, 3, row.get("SP %"), single_thresholds)
        _add_percentage_fill(style, row_index, 6, row.get("MP %"), MULTI_PIN_THRESHOLDS)
    table.setStyle(style)
    return table


def _combinations_pdf_table(
    single: pd.DataFrame,
    multi: pd.DataFrame,
    single_thresholds: tuple[float, float],
) -> Table:
    headers = ["Pin Leave", "Made", "Attempts", "Make %"]
    data = [
        ["Single-Pin Combinations", "", "", "", "", "Multi-Pin Combinations", "", "", ""],
        headers + [""] + headers,
    ]
    max_rows = max(len(single), len(multi), 1)
    for index in range(max_rows):
        single_values = _combination_values(single, index)
        multi_values = _combination_values(multi, index)
        data.append(single_values + [""] + multi_values)

    table = Table(
        data,
        colWidths=[1.3 * inch, 0.55 * inch, 0.65 * inch, 0.65 * inch, 0.2 * inch] * 1
        + [1.3 * inch, 0.55 * inch, 0.65 * inch, 0.65 * inch],
        repeatRows=2,
        hAlign="LEFT",
    )
    style = _base_table_style(font_size=7.2, header_row=1)
    style.add("SPAN", (0, 0), (3, 0))
    style.add("SPAN", (5, 0), (8, 0))
    style.add("BACKGROUND", (0, 0), (3, 0), LIGHT_BLUE)
    style.add("BACKGROUND", (5, 0), (8, 0), LIGHT_BLUE)
    style.add("TEXTCOLOR", (0, 0), (3, 0), NAVY)
    style.add("TEXTCOLOR", (5, 0), (8, 0), NAVY)
    style.add("FONTNAME", (0, 0), (3, 0), "Helvetica-Bold")
    style.add("FONTNAME", (5, 0), (8, 0), "Helvetica-Bold")
    style.add("ALIGN", (0, 0), (3, 0), "LEFT")
    style.add("ALIGN", (5, 0), (8, 0), "LEFT")
    style.add("GRID", (4, 0), (4, -1), 0, colors.white)
    style.add("BACKGROUND", (4, 0), (4, -1), colors.white)
    style.add("ALIGN", (1, 2), (3, -1), "CENTER")
    style.add("ALIGN", (6, 2), (8, -1), "CENTER")
    for index in range(max_rows):
        table_row = index + 2
        if index < len(single):
            _add_percentage_fill(
                style,
                table_row,
                3,
                single.iloc[index]["Make %"],
                single_thresholds,
            )
        if index < len(multi):
            _add_percentage_fill(
                style,
                table_row,
                8,
                multi.iloc[index]["Make %"],
                MULTI_PIN_THRESHOLDS,
            )
    table.setStyle(style)
    return table


def _base_table_style(font_size: float, header_row: int = 0) -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, header_row), (-1, header_row), NAVY),
            ("TEXTCOLOR", (0, header_row), (-1, header_row), colors.white),
            ("FONTNAME", (0, header_row), (-1, header_row), "Helvetica-Bold"),
            ("FONTNAME", (0, header_row + 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("LEADING", (0, 0), (-1, -1), font_size + 2),
            ("GRID", (0, header_row), (-1, -1), 0.4, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TEXTCOLOR", (0, header_row + 1), (-1, -1), TEXT),
        ]
    )


def _frame_detail_pdf_table(frame: pd.DataFrame) -> Table:
    team_frame, baker_frame = split_team_baker_detail(frame)
    columns = [
        column
        for column in ["Frame", "Points", "Ball1", "Ball2", "Ball3", "Statistic"]
        if column in frame.columns
    ]
    data = [
        ["Team", "", "", "", "", "", "", "Baker", "", "", "", "", ""],
        columns + [""] + columns,
    ]
    max_rows = max(len(team_frame), len(baker_frame), 1)
    for index in range(max_rows):
        data.append(
            _frame_detail_values(team_frame, index, columns)
            + [""]
            + _frame_detail_values(baker_frame, index, columns)
        )

    side_widths = [0.42 * inch, 0.46 * inch, 0.4 * inch, 0.4 * inch, 0.4 * inch, 1.48 * inch]
    table = Table(
        data,
        colWidths=side_widths + [0.16 * inch] + side_widths,
        repeatRows=2,
        hAlign="LEFT",
    )
    style = _base_table_style(font_size=6.3, header_row=1)
    style.add("SPAN", (0, 0), (5, 0))
    style.add("SPAN", (7, 0), (12, 0))
    style.add("BACKGROUND", (0, 0), (5, 0), LIGHT_BLUE)
    style.add("BACKGROUND", (7, 0), (12, 0), LIGHT_BLUE)
    style.add("TEXTCOLOR", (0, 0), (5, 0), NAVY)
    style.add("TEXTCOLOR", (7, 0), (12, 0), NAVY)
    style.add("FONTNAME", (0, 0), (5, 0), "Helvetica-Bold")
    style.add("FONTNAME", (7, 0), (12, 0), "Helvetica-Bold")
    style.add("GRID", (6, 0), (6, -1), 0, colors.white)
    style.add("BACKGROUND", (6, 0), (6, -1), colors.white)
    style.add("ALIGN", (0, 2), (4, -1), "CENTER")
    style.add("ALIGN", (7, 2), (11, -1), "CENTER")
    style.add("ALIGN", (5, 2), (5, -1), "LEFT")
    style.add("ALIGN", (12, 2), (12, -1), "LEFT")
    table.setStyle(style)
    return table


def _frame_detail_values(frame: pd.DataFrame, index: int, columns: list[str]) -> list[object]:
    if index >= len(frame):
        return [""] * len(columns)
    row = frame.iloc[index]
    return [
        StatisticChips(row[column])
        if column == "Statistic"
        else _display_detail_value(row[column])
        for column in columns
    ]


def _display_detail_value(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _statistic_chip_colors(label: str):
    normalized = label.casefold()
    for prefix, chip_colors in STATISTIC_CHIP_COLORS.items():
        if normalized.startswith(prefix):
            return chip_colors
    return colors.HexColor("#E7E6E6"), TEXT


def _add_percentage_fill(
    style: TableStyle,
    row: int,
    column: int,
    value: object,
    thresholds: tuple[float, float],
) -> None:
    if value is None or pd.isna(value):
        return
    green_threshold, yellow_threshold = thresholds
    fill = GREEN if float(value) >= green_threshold else YELLOW if float(value) >= yellow_threshold else RED
    style.add("BACKGROUND", (column, row), (column, row), fill)


def _combination_values(frame: pd.DataFrame, index: int) -> list[str]:
    if index >= len(frame):
        return ["", "", "", ""]
    row = frame.iloc[index]
    return [
        str(row["Pin Leave"]),
        str(int(row["Made"])),
        str(int(row["Attempts"])),
        _format_value("Make %", row["Make %"]),
    ]


def _draw_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(document.leftMargin, 0.2 * inch, "Generated by the LTU Spare Conversion Report app")
    canvas.drawRightString(
        letter[0] - document.rightMargin,
        0.2 * inch,
        f"Page {document.page}",
    )
    canvas.restoreState()


def _for_athlete(frame: pd.DataFrame | None, athlete: str) -> pd.DataFrame:
    if frame is None or frame.empty or "athlete" not in frame.columns:
        return pd.DataFrame()
    return frame[frame["athlete"].astype(str) == athlete].copy()


def _for_event(frame: pd.DataFrame | None, event: str | None) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    if event is None:
        return frame.copy()
    if frame.empty or "event" not in frame.columns:
        return frame.iloc[0:0].copy()
    return frame[frame["event"].astype(str) == event].copy()


def _event_summary(
    records: pd.DataFrame,
    strikes: pd.DataFrame,
    baker: pd.DataFrame,
    team_games: pd.DataFrame,
    *,
    include_total: bool = True,
) -> pd.DataFrame:
    events: set[str] = set()
    for frame in (records, strikes, baker, team_games):
        if not frame.empty and "event" in frame.columns:
            events.update(frame["event"].dropna().astype(str))
    rows = [_summary_row(event, records, strikes, baker, team_games) for event in events]
    rows.sort(key=lambda row: (row.pop("_sort_date"), row["Event"]))
    if include_total:
        total = _summary_row("Total", records, strikes, baker, team_games, filter_event=False)
        total.pop("_sort_date")
        rows.append(total)
    return pd.DataFrame(rows)


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
    baker_count = len(selected_baker)
    baker_points = int(selected_baker["points"].sum()) if not selected_baker.empty else 0
    average = selected_team_games["score"].sum() / len(selected_team_games) if len(selected_team_games) else None
    dates = []
    for frame in (selected_records, selected_strikes, selected_baker, selected_team_games):
        if not frame.empty and "game_date" in frame.columns:
            dates.extend(pd.to_datetime(frame["game_date"], errors="coerce").dropna().tolist())
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
        "BKR Frm": baker_count,
        "BKR Avg": _ratio(baker_points, baker_count),
        "AVG": average,
        "_sort_date": min(dates) if dates else pd.Timestamp.max,
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


def _format_value(header: str, value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if header.endswith("%"):
        return f"{float(value):.2%}"
    if header == "AVG":
        return f"{float(value):.2f}"
    if header == "BKR Avg":
        return f"{float(value):.1f}"
    return str(value)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "", value).strip(" .")
    return cleaned or "Athlete"
