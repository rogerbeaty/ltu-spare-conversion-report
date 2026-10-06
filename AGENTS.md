# LTU Spare Conversion Report App — Codex Instructions (macOS + Streamlit)

## Objective
Create a local Streamlit application that replicates the “Option 1” spare-conversion Excel report generator.

The app runs entirely locally on macOS:
- Upload a BowlSheet CSV
- Build the report using the locked Option 1 ruleset
- Download the generated Excel file

## Tech Stack
- Python 3.11+
- Streamlit
- pandas
- openpyxl
- pytest (tests)

## Repository Layout (required)
- app.py
- src/
  - __init__.py
  - parser.py
  - report.py
- tests/
  - test_parser_format_a.py
  - test_parser_format_b.py
- requirements.txt
- README.md

## UI Requirements (Streamlit)
Single page:
- Title: “LTU Spare Conversion Report (Option 1)”
- CSV uploader
- Button: “Build Report”
- Status panel:
  - detected format (A or B)
  - rows processed
  - spare opportunities
  - athletes found
- After build:
  - “Download Excel” button returning bytes

## Input Formats to Support (must autodetect)

### Format A — Frame-based columns
Detect: any column name containing "Frame1Ball1"
Use columns:
- Frame{n}Ball1Bowler
- Frame{n}Ball1
- Frame{n}Ball2
- Frame{n}Ball1Pins
- Frame{n}Ball1Split
Special 10th:
- Frame10Ball2Pins
- Frame10Ball2Split
- Frame10Ball3Bowler

### Format B — 101-column “delivery grid” export
Read with header=None.
Fixed indices:
- bowlers: cols 7..27 (21)
- splits:  cols 38..58 (21) (0/1)
- knocked: cols 59..79 (21)
- leaves:  cols 80..100 (21)
Delivery mapping:
- Frames 1–9: (0,1), (2,3), ..., (16,17)
- Frame 10: (18,19,20)

Autodetect logic:
- If "Frame1Ball1" is present in headers → Format A
- Else → Format B

## Locked Option 1 Logic (must match)
Definitions:
- Strike when ball1 == 10
- Spare opportunity when ball1 < 10
- Spare made when ball1 + ball2 == 10
- Single-pin opp when ball1 == 9
- Multi-pin opp when ball1 < 9
- Split identified by split boolean/flag on ball1 (or 10th special uses ball2 split)
Split rule:
- Exclude split attempts from multi-pin reporting unless spare is made
  (made splits count as multi-pin made/attempted)
Exclude:
- Full rack leave: "1-2-3-4-5-6-7-8-9-10"
Ignore fouls if identifiable:
- If the format exposes explicit foul markers, treat that ball’s knocked pins as 0.
- If not identifiable, do not guess.

10th-frame special case:
If frame10 ball1 is strike AND ball2 < 10 AND ball3 exists:
- Treat ball2 as spare opportunity using ball2 leave string
- Spare made when ball2 + ball3 == 10
- Split from ball2 split
- Attribute the attempt/make to ball3 bowler

Normalized record output (from parser):
Each spare-opportunity record must be:
- Name (string)
- Pins (leave string)
- Type ("Single" or "Multi")
- Split (bool)
- Made (bool)

## Locked Option 1 Presentation (Excel)
Workbook:
- Tab 1: “Overall Summary”
Columns:
Name,
Multi-Pin Made, Multi-Pin Attempts, Multi-Pin %,
Single-Pin Made, Single-Pin Attempts, Single-Pin %

Then one worksheet per athlete:
- Sheet name truncated to 31 chars
- Title row: athlete name, merged across columns, centered, font size 18 bold

Each athlete sheet must contain TWO sections:
1) “Single Pin”
2) “Multi Pin”
Within each section:
- Table columns: Pin Leave, Made, Attempts, Make %
- Common: Attempts >= 10
- Rare: Attempts < 10
Order:
- Common rows first
- Insert bold separator row: “— Rare combinations below —”
- Rare rows grouped/collapsible (Excel outline group), default hidden
Formatting:
- Make % formatted as percentage (0.0%)
- Pin Leave column forced to text ("@")
Highlighting (Common rows only, Make % cell only):
- Green >= 90%
- Yellow >= 80% and < 90%
- Red < 80%

## Tests (pytest)
Create synthetic datasets for both formats that validate:
- spare opp vs strike logic
- single vs multi classification
- split exclusion (missed split multi-pin excluded; made split counted)
- full rack leave exclusion
- 10th-frame special case attribution to ball3 bowler

## README.md requirements (macOS)
Include exact steps:
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt
- streamlit run app.py
Also include “How it works” (short) and supported CSV formats.

## What Codex should do
1) Scaffold repo structure
2) Implement parsing + tests
3) Implement Excel builder + formatting rules
4) Build Streamlit UI
5) Run tests and ensure app can generate a workbook and download it
