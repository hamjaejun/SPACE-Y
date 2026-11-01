from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contract import build_validation_report
from .cooling_hc import solve_cooling_hc_file
from .design_decision import solve_design_decision_file
from .design_space import explore_design_space_file
from .external import probe_cea, probe_rpa, run_cea_request, write_json
from .gas import solve_gas_map_file
from .gas_wall import solve_gas_wall_file
from .m0 import solve_m0_file
from .m0_coupled import solve_coupled_single_segment_file
from .m0_marching import solve_coupled_multisegment_file
from .m0_refinement import solve_refinement_suite_file
from .physics import run_basic_checks


def _emit(value: object, output: str | None) -> None:
    if output:
        write_json(output, value)
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="space-y")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-contract")
    validate.add_argument("contract")
    validate.add_argument("--schema", default="schemas/SPACE_Y_M0_model_contract.schema.json")
    validate.add_argument("--dependencies", default="config/qoi_dependencies.json")
    validate.add_argument("--output")

    physics = sub.add_parser("physics-check")
    physics.add_argument("--output")

    cea_probe = sub.add_parser("cea-probe")
    cea_probe.add_argument("--python", default=sys.executable)
    cea_probe.add_argument("--output")

    cea_run = sub.add_parser("cea-run")
    cea_run.add_argument("request")
    cea_run.add_argument("--python", default=sys.executable)
    cea_run.add_argument("--output")

    rpa = sub.add_parser("rpa-probe")
    rpa.add_argument("--executable", required=True)
    rpa.add_argument("--resources")
    rpa.add_argument("--user-resources")
    rpa.add_argument("--smoke-script")
    rpa.add_argument("--output-prefix")
    rpa.add_argument("--output")

    m0 = sub.add_parser("m0-run")
    m0.add_argument("request")
    m0.add_argument("--schema", default="schemas/SPACE_Y_M0_run.schema.json")
    m0.add_argument("--output")

    gas = sub.add_parser("gas-map")
    gas.add_argument("request")
    gas.add_argument("--schema", default="schemas/SPACE_Y_GAS_mapping.schema.json")
    gas.add_argument("--output")

    gas_wall = sub.add_parser("gas-wall-run")
    gas_wall.add_argument("request")
    gas_wall.add_argument("--schema", default="schemas/SPACE_Y_GAS_WALL_run.schema.json")
    gas_wall.add_argument("--output")

    coupled = sub.add_parser("m0-coupled-run")
    coupled.add_argument("request")
    coupled.add_argument(
        "--schema", default="schemas/SPACE_Y_M0_coupled_single_segment.schema.json"
    )
    coupled.add_argument("--output")

    marching = sub.add_parser("m0-marching-run")
    marching.add_argument("request")
    marching.add_argument(
        "--schema", default="schemas/SPACE_Y_M0_multisegment.schema.json"
    )
    marching.add_argument("--output")

    refinement = sub.add_parser("m0-refinement-run")
    refinement.add_argument("request")
    refinement.add_argument(
        "--schema", default="schemas/SPACE_Y_M0_refinement_suite.schema.json"
    )
    refinement.add_argument("--output")

    decision = sub.add_parser("pdr-decide")
    decision.add_argument("request")
    decision.add_argument(
        "--schema", default="schemas/SPACE_Y_PDR_decision.schema.json"
    )
    decision.add_argument("--output")

    exploration = sub.add_parser("design-space-run")
    exploration.add_argument("request")
    exploration.add_argument(
        "--schema", default="schemas/SPACE_Y_design_space.schema.json"
    )
    exploration.add_argument("--output")

    cooling_hc = sub.add_parser("cooling-hc-run")
    cooling_hc.add_argument("request")
    cooling_hc.add_argument(
        "--schema", default="schemas/SPACE_Y_cooling_hc.schema.json"
    )
    cooling_hc.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-contract":
        result = build_validation_report(args.contract, args.schema, args.dependencies)
    elif args.command == "physics-check":
        result = run_basic_checks()
    elif args.command == "cea-probe":
        result = probe_cea(args.python)
    elif args.command == "cea-run":
        result = run_cea_request(args.request, args.python)
    elif args.command == "rpa-probe":
        result = probe_rpa(
            args.executable,
            resources=args.resources,
            user_resources=args.user_resources,
            smoke_script=args.smoke_script,
            output_prefix=args.output_prefix,
        )
    elif args.command == "m0-run":
        result = solve_m0_file(args.request, args.schema)
    elif args.command == "gas-map":
        result = solve_gas_map_file(args.request, args.schema)
    elif args.command == "gas-wall-run":
        result = solve_gas_wall_file(args.request, args.schema)
    elif args.command == "m0-coupled-run":
        result = solve_coupled_single_segment_file(args.request, args.schema)
    elif args.command == "m0-marching-run":
        result = solve_coupled_multisegment_file(args.request, args.schema)
    elif args.command == "m0-refinement-run":
        result = solve_refinement_suite_file(args.request, args.schema)
    elif args.command == "pdr-decide":
        result = solve_design_decision_file(args.request, args.schema)
    elif args.command == "design-space-run":
        result = explore_design_space_file(args.request, args.schema)
    elif args.command == "cooling-hc-run":
        result = solve_cooling_hc_file(args.request, args.schema)
    else:
        raise AssertionError(args.command)
    _emit(result, args.output)
    if isinstance(result, dict) and result.get("status") in {
        "FAIL", "INPUT_U", "OUTSIDE", "NUM_U", "BLOCKED_BY_INPUT"
    }:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
