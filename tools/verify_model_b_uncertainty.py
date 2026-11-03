from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

try:
    from tools.analyze_model_b_uncertainty import (
        BASE_INPUT,
        OUTPUT_DIR,
        build_uncertainty_analysis,
        write_analysis,
    )
except ModuleNotFoundError:  # Direct execution from the tools directory.
    from analyze_model_b_uncertainty import (
        BASE_INPUT,
        OUTPUT_DIR,
        build_uncertainty_analysis,
        write_analysis,
    )


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    base = json.loads(BASE_INPUT.read_text(encoding="utf-8"))
    analysis = build_uncertainty_analysis(base)
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
        "verification_id": "SPACE_Y_MODEL_B_UNCERTAINTY_VERIFICATION_V1",
        "command": ".venv\\Scripts\\python.exe -m unittest discover -s tests -q",
        "exit_code": completed.returncode,
        "test_count": int(match.group(1)) if match else None,
        "result": "PASS" if completed.returncode == 0 else "FAIL",
        "baseline_test_count_before_followup": 245,
        "production_model_changed_by_followup": False,
        "model_a_preserved": True,
        "transition_friction_supported": False,
        "experimental_validation": False,
        "actual_engine_prediction": False,
        "fluid_classification": "SYNTHETIC_TEST_FLUID_NOT_IPA",
        "verification_scope": [
            "production Model B regression",
            "Model A regression",
            "Re=4000 comparison-path independence",
            "value continuity and slope diagnostics",
            "finite positive Nu and h_l",
            "aspect-ratio and thermal-boundary scenarios",
            "deterministic repeatability",
            "null and invalid denominator handling",
            "statistics and relative-difference arithmetic",
        ],
        "not_evidence_of": [
            "actual IPA property validation",
            "measured uncertainty or statistical confidence interval",
            "transition-model experimental accuracy",
            "SPACE Y engine cooling or safety approval",
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
