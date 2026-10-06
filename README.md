# LTU Spare Conversion Report App (Option 1)

Local Streamlit app that analyzes BowlSheet CSV exports and creates individual athlete PDF reports.

## What it does
- Accepts CSV uploads and stores local copies in `data/uploads/`.
- Lets you reopen or delete previously uploaded data from the app.
- Combines all saved Men's files or all saved Women's files into one selected team view.
- Uses the CSV `Event` field as the tournament grouping and the filename for Men's/Women's classification.
- Detects input format:
  - `Frame-columns` (classic BowlSheet with `Frame{n}Ball{m}...` fields)
  - `Delivery-grid` (101-column export read with fixed indices)
- Applies locked Option 1 rules:
  - Spare opportunities: first ball < 10
  - Spare made: ball1 + ball2 == 10
  - Single pin: first ball == 9
  - Multi pin: first ball < 9
  - Split misses are excluded from multi-pin attempts
  - Made splits are included
  - Full-rack leave (`1-2-3-4-5-6-7-8-9-10`) is excluded
  - Foul markers (`F`, `FOUL`) are treated as 0 knocked pins
  - 10th-frame special case implemented
  - Average uses the sum of `Frame10Score` for Team games divided by the athlete's Team games
  - Mixed-athlete Team games are credited to the athlete who started the game
  - Baker games never affect Average
- Creates a downloadable ZIP containing one portrait PDF report per athlete, plus an individual PDF download when an athlete is selected.
- Provides an Excel download of the displayed Overall Summary when a specific event is selected.
- Uses the limited-data fallback: when no combination has 10 attempts, combinations with 2-9 attempts move to the main table and only one-attempt combinations remain rare.
- Single Pin colors: Men use green >= 90% and yellow >= 80%; Women use green >= 85% and yellow >= 75%.
- Multi Pin colors for both teams: green >= 80% and yellow >= 50%.
- Shows a diagnostic Statistic column in filtered Ball-by-Ball Detail, including multi-stat 10th frames and excluded split misses.
- Orders Ball-by-Ball Detail by the BowlSheet game timestamp, original game row, and frame number.
- Displays event-specific Team ball detail on the left and Baker ball detail on the right; All Events athlete summaries omit frame detail.
- Displays compact Single Pin and Multi Pin combination tables side by side without tabs.

## Conservative interpretation notes
- If the source does not clearly identify foul markers, values are parsed normally.
- In delivery-grid files, format detection defaults to delivery-grid unless a `Frame1Ball1`-style header is present.
- If a bowler name for a ball is missing, parser falls back to the paired delivery bowler when applicable.

## Project structure
- `app.py` - Streamlit UI
- `src/parser.py` - CSV detection and normalization into spare-opportunity records
- `src/report.py` - preserved legacy Excel workbook builder (not exposed by the app)
- `tests/test_parser.py` - parser tests for both formats and split/full-rack behavior

## Run locally
1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run tests:

```bash
pytest
```

4. Start the app:

```bash
streamlit run app.py
```

5. In the browser UI:
- Upload a BowlSheet CSV
- Select previously saved data without uploading it again, if desired
- Use **Delete saved file** to remove the app's local copy
- Use the download icon beside **Bowler Statistics** to download every athlete's PDF report in one ZIP file

Uploaded CSVs remain on this Mac under `data/uploads/` between app restarts. This directory is excluded from Git. Deleting a saved file removes only the app's local copy, not the original CSV elsewhere on the Mac.

## Athlete PDF reports

The download icon beside **Bowler Statistics** follows the Event filter. With **All Events** selected, it creates one ZIP containing a full report for every athlete on the selected team. With a specific event selected, it creates one ZIP containing an event-only PDF for every athlete who appears in that event. Each event PDF includes that athlete's event summary, single-pin and multi-pin combination statistics, and classified frame-by-frame detail with all three ball values. Statistic classifications use the same colored chips as the online event detail. No Google account or network connection is required.

### Team and event grouping

Include `Men` or `Women` in every uploaded filename. The app classifies filenames case-insensitively and provides a Team filter:

- `Men` combines every saved Men's file.
- `Women` combines every saved Women's file.

Each CSV's `Event` value is treated as the tournament name. With `All Events` selected, athlete statistics aggregate across every loaded tournament in the selected team scope. Selecting one event limits the statistics to that tournament.
