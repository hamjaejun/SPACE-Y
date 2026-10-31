from __future__ import annotations

from pathlib import Path
import unittest

from space_y.cea_worker import _validate_request
from space_y.external import sha256_file


class ExternalInterfaceTests(unittest.TestCase):
    def test_hash_is_stable(self) -> None:
        path = Path(__file__).resolve().parents[1] / "examples/cea_h2_o2_hp_non_design.json"
        self.assertEqual(
            sha256_file(path),
            "90437027c383b37fb39d53bc2112d70822845c77b8a71009d96eba0f13c38073",
        )

    def test_cea_request_requires_non_design_marker(self) -> None:
        with self.assertRaises(ValueError):
            _validate_request(
                {
                    "purpose": "DESIGN",
                    "mode": "equilibrium_hp",
                    "reactants": [
                        {"species": "H2", "moles": 2, "temperature_K": 298.15}
                    ],
                }
            )

    def test_tracked_execution_artifacts_do_not_expose_local_absolute_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "artifacts/phase1/cea_capability.json",
            "artifacts/phase1/cea_h2_o2_hp_run.json",
            "artifacts/phase1/cea_rp1311_example13_rocket_run.json",
            "artifacts/phase1/rpa_capability.json",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\", text)
            self.assertNotIn("C:/Users/", text)
            self.assertNotIn("C:\\Program Files\\", text)

    def test_public_project_files_do_not_expose_user_or_install_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        local_account_name = Path.home().name
        candidates = []
        for directory in ("artifacts", "config", "docs", "examples", "schemas", "src"):
            candidates.extend(
                path
                for path in (root / directory).rglob("*")
                if path.is_file() and path.suffix.lower() in {".json", ".md", ".py"}
            )
        candidates.extend(root.glob("*.md"))
        candidates.extend(root.glob("*.json"))
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\", text, path)
            self.assertNotIn("C:/Users/", text, path)
            self.assertNotIn("C:\\Program Files\\", text, path)
            self.assertNotIn(local_account_name, text, path)


if __name__ == "__main__":
    unittest.main()
