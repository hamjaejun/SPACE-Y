from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from .coolant import (
    CoolantGeometry,
    CoolantStop,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_geometry,
    build_single_phase_domain,
    check_single_phase_bulk_state,
    evaluate_local_coolant_closures,
    resolve_coolant_flow,
)
from .m0 import FluidState


INTERFACE_VERSION = "SPACE_Y_COOLING_HC_V1"


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoolantStop("INPUT_U", f"{name} source is required")
    return value.strip()


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CoolantStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _failure(run_id: Any, status: str, reason: str, **details: Any) -> dict[str, Any]:
    result = {
        "interface_version": INTERFACE_VERSION,
        "run_id": run_id,
        "status": status,
        "numerical_status": "NOT_RUN",
        "applicability_status": status,
        "reason": reason,
        "results": None,
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
        "safety_approval": False,
    }
    result.update(details)
    return result


def _build_state(spec: dict[str, Any]) -> FluidState:
    _source(spec.get("property_source"), "fluid property")
    _source(spec.get("property_version"), "fluid property version")
    canonical_name = str(spec.get("canonical_name", "")).strip().casefold()
    cas_number = str(spec.get("cas_number", "")).strip()
    if canonical_name in {"2-propanol", "isopropanol", "ipa"} and cas_number != "67-63-0":
        raise CoolantStop("INPUT_U", "2-propanol must use CAS 67-63-0")
    if cas_number == "67-63-0" and canonical_name not in {
        "2-propanol",
        "isopropanol",
        "ipa",
    }:
        raise CoolantStop("INPUT_U", "CAS 67-63-0 identity must be labelled as 2-propanol/IPA")

    enthalpy = float(spec.get("enthalpy_J_kg"))
    if not math.isfinite(enthalpy):
        raise CoolantStop("INPUT_U", "fluid enthalpy must be finite")
    return FluidState(
        temperature_K=_positive(spec.get("temperature_K"), "fluid temperature"),
        pressure_Pa_abs=_positive(spec.get("pressure_Pa_abs"), "fluid absolute pressure"),
        enthalpy_J_kg=enthalpy,
        density_kg_m3=_positive(spec.get("density_kg_m3"), "fluid density"),
        viscosity_Pa_s=_positive(spec.get("viscosity_Pa_s"), "fluid viscosity"),
        cp_J_kgK=_positive(spec.get("cp_J_kgK"), "fluid heat capacity"),
        conductivity_W_mK=_positive(
            spec.get("conductivity_W_mK"), "fluid thermal conductivity"
        ),
    )


def solve_cooling_hc(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one prescribed coolant segment without choosing a design.

    The function is intentionally property-provider neutral.  It consumes a
    state that has already been established by a Property Adapter and checks
    that state against an independently supplied single-phase domain.
    """

    run_id = request.get("run_id")
    try:
        purpose = request.get("purpose")
        if purpose not in {
            "NON_DESIGN_ARITHMETIC_VERIFICATION",
            "ENGINE_ANALYSIS",
        }:
            raise CoolantStop("INPUT_U", "unsupported or missing run purpose")
        if purpose == "ENGINE_ANALYSIS" and not request.get("input_evidence_complete"):
            raise CoolantStop(
                "INPUT_U", "engine analysis requires input_evidence_complete=true"
            )

        state_spec = request["fluid_state"]
        flow_spec = request["flow_conditions"]
        geometry_spec = request["channel_geometry"]
        model_spec = request["model_conditions"]

        state = _build_state(state_spec)
        phase_domain = build_single_phase_domain(model_spec["single_phase_domain"])
        if not phase_domain.classification.startswith("SINGLE_PHASE_"):
            raise CoolantStop(
                "OUTSIDE", "coolant closure requires an independently confirmed single-phase state"
            )
        check_single_phase_bulk_state(
            phase_domain, state.temperature_K, state.pressure_Pa_abs
        )

        derived_geometry = build_rectangular_channel_geometry(geometry_spec)
        _source(geometry_spec.get("geometry_source"), "channel geometry")
        _source(geometry_spec.get("roughness_source"), "equivalent sand roughness")
        _source(geometry_spec.get("local_loss_source"), "local loss coefficient")
        roughness = float(geometry_spec.get("equivalent_sand_roughness_m"))
        local_k = float(geometry_spec.get("local_loss_coefficient"))
        if not math.isfinite(roughness) or not math.isfinite(local_k):
            raise CoolantStop("INPUT_U", "roughness and local loss must be finite")

        flow = resolve_coolant_flow(
            flow_spec,
            channel_count=derived_geometry.channel_count,
            relative_tolerance=float(flow_spec.get("consistency_relative_tolerance", 1.0e-9)),
        )
        _source(flow_spec.get("coolant_mass_flow_source"), "coolant mass flow")

        heat_domain = build_heat_transfer_domain(model_spec["heat_transfer_domain"])
        friction_domain = build_friction_domain(model_spec["friction_domain"])
        thermal_boundary = str(model_spec.get("thermal_boundary_condition", ""))
        if not thermal_boundary:
            raise CoolantStop("INPUT_U", "thermal boundary condition is required")
        _source(
            model_spec.get("thermal_boundary_condition_source"),
            "thermal boundary condition",
        )
        if model_spec.get("fully_developed") is None:
            raise CoolantStop("INPUT_U", "fully developed condition must be explicit")
        _source(
            model_spec.get("fully_developed_evidence_source"),
            "fully developed condition",
        )
        closure_geometry = str(model_spec.get("closure_geometry", ""))
        if not closure_geometry:
            raise CoolantStop("INPUT_U", "closure geometry is required")
        heat_transfer_boundary = str(
            model_spec.get("heat_transfer_thermal_boundary_condition", "")
        )
        if not heat_transfer_boundary:
            raise CoolantStop(
                "INPUT_U", "heat-transfer correlation thermal boundary is required"
            )

        geometry = CoolantGeometry(
            length_m=derived_geometry.length_m,
            flow_area_m2=derived_geometry.total_flow_area_m2,
            hydraulic_diameter_m=derived_geometry.hydraulic_diameter_m,
            coolant_wetted_area_m2=derived_geometry.total_internal_wetted_area_m2,
            equivalent_sand_roughness_m=roughness,
            local_loss_coefficient=local_k,
            closure_geometry=closure_geometry,
            fully_developed=bool(model_spec["fully_developed"]),
            thermal_boundary_condition=thermal_boundary,
        )
        local = evaluate_local_coolant_closures(
            state=state,
            mass_flow_kg_s=flow.coolant_mass_flow_kg_s,
            geometry=geometry,
            heat_transfer_domain=heat_domain,
            heat_transfer_thermal_boundary_condition=heat_transfer_boundary,
            friction_domain=friction_domain,
        )

        return {
            "interface_version": INTERFACE_VERSION,
            "run_id": run_id,
            "status": "MODEL_U",
            "numerical_status": "PASS",
            "applicability_status": "APPLICABLE_TO_SUPPLIED_DOMAIN",
            "fixture_classification": (
                "SYNTHETIC_NON_DESIGN"
                if purpose == "NON_DESIGN_ARITHMETIC_VERIFICATION"
                else "ENGINE_INPUT"
            ),
            "inputs_consumed": {
                "fluid_state": asdict(state),
                "fluid_identity": {
                    "canonical_name": state_spec.get("canonical_name"),
                    "cas_number": state_spec.get("cas_number"),
                    "property_source": state_spec.get("property_source"),
                    "property_version": state_spec.get("property_version"),
                },
                "flow_conditions": asdict(flow),
                "channel_geometry": asdict(derived_geometry),
                "model_conditions": {
                    "single_phase_domain": asdict(phase_domain),
                    "heat_transfer_domain": asdict(heat_domain),
                    "friction_domain": asdict(friction_domain),
                    "thermal_boundary_condition": thermal_boundary,
                    "fully_developed": geometry.fully_developed,
                },
            },
            "results": asdict(local),
            "checks": {
                "mass_distribution": flow.distribution_status,
                "branch_accounting": flow.branch_accounting_status,
                "single_phase_bulk_state": "WITHIN_SUPPLIED_DOMAIN",
                "geometry_layout": derived_geometry.layout_check_status,
                "heat_transfer_correlation": local.heat_transfer_applicability["status"],
                "friction_correlation": local.friction_applicability["status"],
            },
            "assumptions": [
                "ASSUMED_EQUAL_DISTRIBUTION",
                "STEADY_ONE_DIMENSIONAL_LOCAL_CLOSURE",
                "CLOSED_RECTANGULAR_DUCT_HYDRAULIC_DIAMETER",
                "PRESCRIBED_BULK_FLUID_STATE",
            ],
            "not_implemented": [
                "parallel-manifold maldistribution",
                "boiling or two-phase heat transfer",
                "property extrapolation",
                "automatic coolant-flow or channel-dimension selection",
                "gas-side, wall, structural, or supply-system solution",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
            "safety_approval": False,
        }
    except CoolantStop as error:
        return _failure(run_id, error.status, str(error))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(run_id, "INPUT_U", f"invalid or missing input: {error}")
    except RuntimeError as error:
        return _failure(run_id, "NUM_U", str(error))


def solve_cooling_hc_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("cooling-hc request must be a JSON object")
    if schema_path is not None:
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            errors = sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(request),
                key=lambda item: list(item.path),
            )
            if errors:
                return _failure(
                    request.get("run_id"),
                    "INPUT_U",
                    "cooling-hc request schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path)
                            or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    return solve_cooling_hc(request)
