from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


def _array(value: Any) -> list[Any]:
    return value.tolist() if hasattr(value, "tolist") else list(value)


def _validate_request(request: dict[str, Any]) -> None:
    if request.get("purpose") != "NON_DESIGN_INTERFACE_VERIFICATION":
        raise ValueError("request purpose must explicitly identify a non-design interface check")
    if request.get("mode") not in {"equilibrium_hp", "rocket"}:
        raise ValueError("mode must be equilibrium_hp or rocket")
    reactants = request.get("reactants")
    if not isinstance(reactants, list) or not reactants:
        raise ValueError("reactants must be a non-empty array")
    for item in reactants:
        if not isinstance(item, dict) or not isinstance(item.get("species"), str):
            raise ValueError("each reactant requires a species string")
        if ("moles" in item) == ("mass_fraction" in item):
            raise ValueError("each reactant requires exactly one of moles or mass_fraction")
        if item.get("temperature_K") is None:
            raise ValueError("each reactant requires temperature_K")


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("reference-check path must be a JSON Pointer")
    current = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _reference_checks(result: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for spec in request.get("reference_checks", []):
        actual = float(_resolve_pointer(result, spec["path"]))
        expected = float(spec["expected"])
        absolute_tolerance = float(spec["absolute_tolerance"])
        checks.append(
            {
                "path": spec["path"],
                "actual": actual,
                "expected": expected,
                "absolute_tolerance": absolute_tolerance,
                "pass": abs(actual - expected) <= absolute_tolerance,
                "source": spec.get("source"),
            }
        )
    return checks


def solve(request: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import cea

    _validate_request(request)
    reactants_spec = request["reactants"]
    names = [item["species"] for item in reactants_spec]
    temperatures = np.array([float(item["temperature_K"]) for item in reactants_spec])
    reactants = cea.Mixture(names)
    if "moles" in reactants_spec[0]:
        amounts = np.array([float(item["moles"]) for item in reactants_spec])
        weights = reactants.moles_to_weights(amounts)
    else:
        weights = np.array([float(item["mass_fraction"]) for item in reactants_spec])
        weights = weights / weights.sum()
    normalized_weights = weights / weights.sum()
    initial_enthalpy = float(reactants.calc_property(cea.ENTHALPY, weights, temperatures))
    products = cea.Mixture(names, products_from_reactants=True)

    if request["mode"] == "equilibrium_hp":
        pressure_bar = float(request["pressure_bar_abs"])
        solver = cea.EqSolver(products, reactants=reactants)
        solution = cea.EqSolution(solver)
        solver.solve(solution, cea.HP, initial_enthalpy / cea.R, pressure_bar, weights)
        result = {
            "cea_version": cea.__version__,
            "mode": request["mode"],
            "converged": bool(solution.converged),
            "last_error": int(solution.last_error),
            "temperature_K": float(solution.T),
            "pressure_bar_abs": pressure_bar,
            "molecular_weight_kg_per_kmol": float(solution.MW),
            "initial_enthalpy_J_per_kg": initial_enthalpy,
            "reactant_weight_amounts": _array(weights),
            "reactant_mass_fractions": _array(normalized_weights),
            "product_mole_fractions": {
                key: _array(value) for key, value in solution.mole_fractions.items()
            },
            "source_note": request.get("source_note"),
        }
        result["reference_checks"] = _reference_checks(result, request)
        return result

    pressure_bar = float(request["chamber_pressure_bar_abs"])
    ratios = np.array([float(value) for value in request["chamber_to_exit_pressure_ratios"]])
    rocket_options: dict[str, Any] = {
        "reactants": reactants,
        "transport": bool(request.get("transport", False)),
        "ions": bool(request.get("ions", False)),
    }
    if request.get("trace") is not None:
        rocket_options["trace"] = float(request["trace"])
    if request.get("insert_species") is not None:
        rocket_options["insert"] = list(request["insert_species"])
    solver = cea.RocketSolver(products, **rocket_options)
    solution = cea.RocketSolution(solver)
    kwargs: dict[str, Any] = {
        "hc": initial_enthalpy / cea.R,
        "iac": bool(request.get("infinite_area_combustor", True)),
    }
    if request.get("frozen_at_station") is not None:
        kwargs["n_frz"] = int(request["frozen_at_station"])
    solver.solve(solution, weights, pressure_bar, ratios, **kwargs)
    fields = (
        "T",
        "P",
        "MW",
        "gamma_s",
        "Mach",
        "ae_at",
        "c_star",
        "coefficient_of_thrust",
        "Isp",
        "Isp_vacuum",
        "enthalpy",
        "sonic_velocity",
    )
    station_data = {field: _array(getattr(solution, field)) for field in fields}
    station_units = {
        "T": "K",
        "P": "bar(a)",
        "MW": "kg/kmol",
        "gamma_s": "dimensionless",
        "Mach": "dimensionless",
        "ae_at": "dimensionless",
        "c_star": "m/s",
        "coefficient_of_thrust": "dimensionless",
        "Isp": "m/s",
        "Isp_vacuum": "m/s",
        "enthalpy": "kJ/kg",
        "sonic_velocity": "m/s",
    }
    transport_data = None
    if bool(request.get("transport", False)):
        transport_data = {
            "basis_available": ["frozen", "equilibrium"],
            "original_units": {
                "viscosity": "millipoise",
                "conductivity": "mW/(cm K)",
                "cp": "kJ/(kg K)",
                "Pr": "dimensionless",
            },
            "original": {
                "viscosity_millipoise": _array(solution.viscosity),
                "conductivity_fr_mW_cmK": _array(solution.conductivity_fr),
                "conductivity_eq_mW_cmK": _array(solution.conductivity_eq),
                "cp_fr_kJ_kgK": _array(solution.cp_fr),
                "cp_eq_kJ_kgK": _array(solution.cp_eq),
                "Pr_fr": _array(solution.Pr_fr),
                "Pr_eq": _array(solution.Pr_eq),
            },
            "si": {
                "viscosity_Pa_s": _array(solution.viscosity * 1.0e-4),
                "conductivity_fr_W_mK": _array(solution.conductivity_fr * 1.0e-1),
                "conductivity_eq_W_mK": _array(solution.conductivity_eq * 1.0e-1),
                "cp_fr_J_kgK": _array(solution.cp_fr * 1.0e3),
                "cp_eq_J_kgK": _array(solution.cp_eq * 1.0e3),
                "Pr_fr": _array(solution.Pr_fr),
                "Pr_eq": _array(solution.Pr_eq),
            },
        }
    result = {
        "cea_version": cea.__version__,
        "mode": request["mode"],
        "converged": bool(solution.converged),
        "last_error": int(solution.last_error),
        "initial_enthalpy_J_per_kg": initial_enthalpy,
        "reactant_weight_amounts": _array(weights),
        "reactant_mass_fractions": _array(normalized_weights),
        "stations": station_data,
        "station_units": station_units,
        "transport": transport_data,
        "product_mass_fractions": {
            key: _array(value) for key, value in solution.mass_fractions.items()
        },
        "source_note": request.get("source_note"),
    }
    result["reference_checks"] = _reference_checks(result, request)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m space_y.cea_worker REQUEST.json", file=sys.stderr)
        return 2
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    result = solve(request)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    checks_pass = all(check["pass"] for check in result.get("reference_checks", []))
    return 0 if result.get("converged") and checks_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
