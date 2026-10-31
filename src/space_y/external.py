from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _portable_path(path: str | Path, *, relative_to: str | Path | None = None) -> str:
    resolved = Path(path).resolve()
    if relative_to is not None:
        try:
            return resolved.relative_to(Path(relative_to).resolve()).as_posix()
        except ValueError:
            pass
    substitutions = (
        (os.environ.get("ProgramFiles"), "<PROGRAM_FILES>"),
        (os.environ.get("LOCALAPPDATA"), "<LOCAL_APP_DATA>"),
        (os.environ.get("USERPROFILE"), "<USER_HOME>"),
    )
    for root, label in substitutions:
        if root:
            try:
                suffix = resolved.relative_to(Path(root).resolve()).as_posix()
                return f"{label}/{suffix}"
            except ValueError:
                pass
    return resolved.name


def probe_cea(python_executable: str | Path = sys.executable) -> dict[str, Any]:
    execution_command = [
        str(python_executable),
        "-c",
        "import json,sys,cea; print(json.dumps({'version':cea.__version__,'python':sys.version}))",
    ]
    completed = subprocess.run(
        execution_command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=30,
    )
    details: dict[str, Any] = {
        "available": completed.returncode == 0,
        "command": [
            _portable_path(python_executable, relative_to=Path.cwd()),
            *execution_command[1:],
        ],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    if completed.returncode == 0:
        details.update(json.loads(completed.stdout.strip()))
    else:
        details["reason"] = "NASA CEA Python binding is not importable by the selected interpreter"
    return details


def run_cea_request(
    request_path: str | Path,
    python_executable: str | Path = sys.executable,
    *,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    request_path = Path(request_path).resolve()
    source_root = Path(__file__).resolve().parents[1]
    repository_root = source_root.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), env.get("PYTHONPATH", "")) if part
    )
    env["PYTHONIOENCODING"] = "utf-8"
    command = [str(python_executable), "-m", "space_y.cea_worker", str(request_path)]
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=timeout_s,
        env=env,
    )
    record: dict[str, Any] = {
        "backend": "NASA_CEA_PYTHON",
        "request_path": _portable_path(request_path, relative_to=repository_root),
        "request_sha256": sha256_file(request_path),
        "command": [
            _portable_path(python_executable, relative_to=repository_root),
            "-m",
            "space_y.cea_worker",
            _portable_path(request_path, relative_to=repository_root),
        ],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "validated_engine_prediction": False,
    }
    if completed.returncode == 0:
        record["result"] = json.loads(completed.stdout)
    else:
        record["stdout"] = completed.stdout
    return record


def probe_rpa(
    executable: str | Path,
    *,
    resources: str | Path | None = None,
    user_resources: str | Path | None = None,
    smoke_script: str | Path | None = None,
    output_prefix: str | Path | None = None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    executable = Path(executable).resolve()
    executable_display = "<RPA_EXECUTABLE>"
    help_run = subprocess.run(
        [str(executable), "--help"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        cwd=str(executable.parent),
    )
    result: dict[str, Any] = {
        "backend": "RPA_SCRIPTING_UTILITY",
        "available": help_run.returncode == 0,
        "executable": executable_display,
        "executable_sha256": sha256_file(executable) if executable.is_file() else None,
        "help_returncode": help_run.returncode,
        "help_stdout": help_run.stdout.replace(str(executable), executable_display),
        "help_stderr": help_run.stderr,
        "platform": platform.platform(),
        "status": "CAPABILITY_ONLY" if help_run.returncode == 0 else "FAIL",
        "smoke_test": "NOT_RUN",
        "validated_engine_prediction": False,
    }
    if smoke_script is None:
        return result
    if output_prefix is None:
        raise ValueError("output_prefix is required when smoke_script is supplied")

    prefix = Path(output_prefix).resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    command: list[str] = [str(executable)]
    display_command: list[str] = [executable_display]
    if resources is not None:
        command += ["-r", str(Path(resources).resolve())]
        display_command += ["-r", "<RPA_RESOURCES>"]
    if user_resources is not None:
        command += ["-ur", str(Path(user_resources).resolve())]
        display_command += ["-ur", "<RPA_USER_RESOURCES>"]
    command += ["-i", str(Path(smoke_script).resolve()), "-o", str(Path(output_prefix).resolve())]
    display_command += [
        "-i",
        "<RPA_SMOKE_SCRIPT>",
        "-o",
        _portable_path(output_prefix, relative_to=Path.cwd()),
    ]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_s,
        cwd=str(executable.parent),
    )
    logs = sorted(prefix.parent.glob(prefix.name + "*.log"))
    diagnostic_lines = [
        line.strip()
        for line in (completed.stdout + "\n" + completed.stderr).splitlines()
        if line.strip().startswith(("ERROR:", "WARNING:"))
    ]
    error_lines = [line for line in diagnostic_lines if line.startswith("ERROR:")]
    warning_lines = [line for line in diagnostic_lines if line.startswith("WARNING:")]
    for path in logs:
        if "warn" in path.name.lower() and path.stat().st_size:
            warning_lines.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            )
    if completed.returncode != 0 or error_lines or not logs:
        smoke_status = "FAIL"
    elif warning_lines:
        smoke_status = "PASS_WITH_WARNINGS"
    else:
        smoke_status = "PASS"
    result.update(
        {
            "status": smoke_status,
            "smoke_test": smoke_status,
            "smoke_command": display_command,
            "smoke_returncode": completed.returncode,
            "smoke_stdout_sha256": _sha256_text(completed.stdout),
            "smoke_stdout_bytes": len(completed.stdout.encode("utf-8", errors="replace")),
            "smoke_stderr": completed.stderr,
            "error_lines": sorted(set(error_lines)),
            "warning_lines": sorted(set(warning_lines)),
            "script_sha256": sha256_file(smoke_script),
            "logs": [
                {
                    "path": _portable_path(path, relative_to=Path.cwd()),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in logs
            ],
        }
    )
    return result


def write_json(path: str | Path, value: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
