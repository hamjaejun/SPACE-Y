from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

from analyze_model_b_candidates import BASE_INPUT, OUTPUT_DIR, build_analysis, write_analysis


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    analysis = build_analysis(json.loads(BASE_INPUT.read_text(encoding="utf-8")))
    write_analysis(OUTPUT_DIR, analysis)
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    match = re.search(r"Ran (\d+) tests", combined)
    report = {
        "verification_id": "SPACE_Y_MODEL_B_FOLLOWUP_VERIFICATION_V1",
        "command": ".venv\\Scripts\\python.exe -m unittest discover -s tests -q",
        "exit_code": completed.returncode,
        "test_count": int(match.group(1)) if match else None,
        "result": "PASS" if completed.returncode == 0 else "FAIL",
        "experimental_validation": False,
        "actual_engine_prediction": False,
        "fluid_classification": "SYNTHETIC_TEST_FLUID_NOT_IPA",
        "scope": [
            "software regression",
            "endpoint continuity",
            "finite/positive arithmetic",
            "candidate comparison reproducibility",
            "transition friction remains unsupported",
        ],
        "not_evidence_of": [
            "actual IPA property validation",
            "entrance-region validation",
            "transition-model experimental accuracy",
            "SPACE Y engine safety or design approval",
        ],
    }
    (OUTPUT_DIR / "verification_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(combined, end="")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
