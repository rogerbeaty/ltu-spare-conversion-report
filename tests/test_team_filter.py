from __future__ import annotations

import ast
from pathlib import Path

from src.dataset import JV, VARSITY, WOMEN, classify_team_from_filename


def test_report_file_selection_excludes_jv_with_stale_varsity_metadata() -> None:
    """Exercise the app's selection expression without starting authenticated UI."""
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text())
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "file_squads"
                for target in node.targets)
    )
    jv = Path("hash__LTU-Women-JV-WHAC-2.csv")
    varsity = Path("hash__LTU-Women-Varsity-WHAC-2.csv")
    team_named_jv = Path("hash__LTU-Women-other.csv")
    scope = {
        "saved_files": [jv, varsity, team_named_jv],
        "upload_metadata": {
            jv.name: (WOMEN, VARSITY),
            varsity.name: (WOMEN, VARSITY),
            team_named_jv.name: (WOMEN, JV),
        },
        "JV": JV,
        "VARSITY": VARSITY,
        "classify_team_from_filename": classify_team_from_filename,
    }
    expression = ast.Expression(body=assignment.value)
    squads = eval(compile(expression, str(app_path), "eval"), scope)
    assert squads[jv] == JV
    assert squads[team_named_jv] == JV
    assert [path for path in scope["saved_files"] if squads[path] == VARSITY] == [varsity]
