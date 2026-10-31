from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from .physics import (
    CorrelationDomain,
    colebrook_darcy_friction_factor,
    coolant_outlet_enthalpy_J_kg,
    darcy_weisbach_pressure_drop_Pa,
    gnielinski_common_form,
    prandtl_number,
    reynolds_number,
    wall_energy_residual_W,
)


class M0Stop(RuntimeError):
    def __init__(self, status: str, message: str, *, segment_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.segment_id = segment_id


@dataclass(frozen=True)
class FluidState:
    temperature_K: float
    pressure_Pa_abs: float
    enthalpy_J_kg: float
    density_kg_m3: float
    viscosity_Pa_s: float
    cp_J_kgK: float
    conductivity_W_mK: float


class M0PropertyAdapter(Protocol):
    """Minimum property seam consumed by the current M0 solvers."""

    def state_pt(self, pressure_Pa_abs: float, temperature_K: float) -> FluidState: ...

    def state_ph(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> FluidState: ...


class PropertyTable:
    """Pressure-bounded, temperature-tabulated single-phase property model.

    Interpolation is linear and extrapolation is prohibited. This is an
    interface implementation, not an assertion that pressure-independent
    interpolation is adequate for a particular coolant.
    """

    def __init__(self, spec: dict[str, Any]):
        if spec.get("model") != "TABULATED_T_PRESSURE_BOUNDED":
            raise M0Stop("INPUT_U", "unsupported or missing property model")
        self.source = spec.get("source")
        if not self.source:
            raise M0Stop("INPUT_U", "property table source is required")
        self.p_min = float(spec["pressure_range_Pa_abs"][0])
        self.p_max = float(spec["pressure_range_Pa_abs"][1])
        self.rows = sorted(spec["rows"], key=lambda row: float(row["temperature_K"]))
        if len(self.rows) < 2:
            raise M0Stop("INPUT_U", "property table needs at least two rows")
        temperatures = [float(row["temperature_K"]) for row in self.rows]
        enthalpies = [float(row["enthalpy_J_kg"]) for row in self.rows]
        if any(b <= a for a, b in zip(temperatures, temperatures[1:])):
            raise M0Stop("INPUT_U", "property-table temperatures must increase")
        if any(b <= a for a, b in zip(enthalpies, enthalpies[1:])):
            raise M0Stop("INPUT_U", "property-table enthalpies must increase")

    def _pressure_check(self, pressure_Pa_abs: float) -> None:
        if not self.p_min <= pressure_Pa_abs <= self.p_max:
            raise M0Stop("OUTSIDE", "coolant pressure is outside the supplied property range")

    def _bracket(self, key: str, value: float) -> tuple[dict[str, Any], dict[str, Any], float]:
        values = [float(row[key]) for row in self.rows]
        if not values[0] <= value <= values[-1]:
            raise M0Stop("OUTSIDE", f"coolant {key} is outside the supplied property range")
        for left, right in zip(self.rows, self.rows[1:]):
            a, b = float(left[key]), float(right[key])
            if a <= value <= b:
                fraction = (value - a) / (b - a)
                return left, right, fraction
        return self.rows[-2], self.rows[-1], 1.0

    @staticmethod
    def _lerp(left: dict[str, Any], right: dict[str, Any], fraction: float, key: str) -> float:
        return float(left[key]) + fraction * (float(right[key]) - float(left[key]))

    def at_temperature(self, pressure_Pa_abs: float, temperature_K: float) -> FluidState:
        self._pressure_check(pressure_Pa_abs)
        left, right, fraction = self._bracket("temperature_K", temperature_K)
        values = {
            key: self._lerp(left, right, fraction, key)
            for key in (
                "enthalpy_J_kg",
                "density_kg_m3",
                "viscosity_Pa_s",
                "cp_J_kgK",
                "conductivity_W_mK",
            )
        }
        if min(
            values["density_kg_m3"],
            values["viscosity_Pa_s"],
            values["cp_J_kgK"],
            values["conductivity_W_mK"],
        ) <= 0:
            raise M0Stop("INPUT_U", "tabulated thermophysical properties must be positive")
        return FluidState(temperature_K, pressure_Pa_abs, **values)

    def at_enthalpy(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> FluidState:
        self._pressure_check(pressure_Pa_abs)
        left, right, fraction = self._bracket("enthalpy_J_kg", enthalpy_J_kg)
        temperature = self._lerp(left, right, fraction, "temperature_K")
        return self.at_temperature(pressure_Pa_abs, temperature)

    # Provider-neutral names used by the solver. The legacy names remain for
    # compatibility with existing arithmetic fixtures.
    def state_pt(self, pressure_Pa_abs: float, temperature_K: float) -> FluidState:
        return self.at_temperature(pressure_Pa_abs, temperature_K)

    def state_ph(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> FluidState:
        return self.at_enthalpy(pressure_Pa_abs, enthalpy_J_kg)


def build_m0_property_adapter(spec: dict[str, Any]) -> M0PropertyAdapter:
    """Build the currently approved M0 provider.

    Only the bounded table used by the synthetic verification suite is wired
    at this stage. A real IPA backend must pass the independent readiness and
    capability gates in ``space_y.property_adapter`` before it is added here.
    """

    return PropertyTable(spec)


def _positive(value: Any, name: str, segment_id: str | None = None) -> float:
    number = float(value)
    if number <= 0:
        raise M0Stop("INPUT_U", f"{name} must be positive", segment_id=segment_id)
    return number


def _domain(spec: dict[str, Any]) -> CorrelationDomain:
    try:
        return CorrelationDomain(
            source=str(spec["source"]),
            re_min=float(spec["re_range"][0]),
            re_max=float(spec["re_range"][1]),
            pr_min=float(spec["pr_range"][0]),
            pr_max=float(spec["pr_range"][1]),
            geometry=str(spec["geometry"]),
            fully_developed_required=bool(spec["fully_developed_required"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise M0Stop("INPUT_U", f"invalid I18 closure domain: {error}") from error


def _validate_run(run: dict[str, Any]) -> None:
    if run.get("purpose") != "NON_DESIGN_VERIFICATION" and not run.get("input_evidence_complete"):
        raise M0Stop("INPUT_U", "design runs require input_evidence_complete=true")
    if run.get("flow_boundary_mode") != "PRESCRIBED_MASS_FLOW":
        raise M0Stop("MODEL_U", "only PRESCRIBED_MASS_FLOW is implemented in this M0 increment")
    if run.get("model_scope") != "STEADY_SINGLE_PATH_1D":
        raise M0Stop("MODEL_U", "only STEADY_SINGLE_PATH_1D is implemented")
    if not run.get("segments"):
        raise M0Stop("INPUT_U", "at least one segment is required")
    ids = [segment.get("id") for segment in run["segments"]]
    if None in ids or len(ids) != len(set(ids)):
        raise M0Stop("INPUT_U", "segment IDs must be present and unique")


def solve_m0(run: dict[str, Any]) -> dict[str, Any]:
    """Solve a steady prescribed-flow, single-path 1D conjugate model."""
    try:
        _validate_run(run)
        table = build_m0_property_adapter(run["coolant_property_model"])
        domain = _domain(run["coolant_closure"])
        boundary = run["boundary"]
        mass_flow = _positive(boundary["mass_flow_kg_s"], "mass flow")
        pressure = _positive(boundary["inlet_pressure_Pa_abs"], "inlet absolute pressure")
        temperature = _positive(boundary["inlet_temperature_K"], "inlet temperature")
        inlet = table.state_pt(pressure, temperature)
        enthalpy = inlet.enthalpy_J_kg

        numerics = run["numerics"]
        max_iterations = int(numerics["max_segment_iterations"])
        relaxation = float(numerics["relaxation"])
        temperature_tolerance = _positive(numerics["temperature_tolerance_K"], "temperature tolerance")
        pressure_tolerance = _positive(numerics["pressure_tolerance_Pa"], "pressure tolerance")
        energy_tolerance = _positive(numerics["energy_residual_tolerance_W"], "energy tolerance")
        if max_iterations <= 0 or not 0 < relaxation <= 1:
            raise M0Stop("INPUT_U", "invalid iteration count or relaxation")

        segment_results: list[dict[str, Any]] = []
        all_applicable = True
        all_converged = True
        total_heat = 0.0
        start_pressure = pressure
        min_pressure = pressure

        for segment in run["segments"]:
            segment_id = str(segment["id"])
            geometry = segment["geometry"]
            gas = segment["gas_boundary"]
            length = _positive(geometry["length_m"], "length", segment_id)
            gas_area = _positive(geometry["gas_heated_area_m2"], "gas heated area", segment_id)
            coolant_area = _positive(geometry["coolant_wetted_area_m2"], "coolant wetted area", segment_id)
            flow_area = _positive(geometry["flow_area_m2"], "flow area", segment_id)
            hydraulic_diameter = _positive(geometry["hydraulic_diameter_m"], "hydraulic diameter", segment_id)
            wall_resistance = _positive(geometry["wall_resistance_K_W"], "wall resistance", segment_id)
            surface_efficiency = float(geometry["surface_efficiency"])
            if not 0 < surface_efficiency <= 1:
                raise M0Stop("INPUT_U", "surface efficiency must be in (0,1]", segment_id=segment_id)
            roughness = float(geometry["equivalent_sand_roughness_m"])
            local_k = float(geometry["local_loss_coefficient"])
            if roughness < 0 or local_k < 0:
                raise M0Stop("INPUT_U", "roughness and local loss must be non-negative", segment_id=segment_id)
            h_g = _positive(gas["h_g_W_m2K"], "gas heat-transfer coefficient", segment_id)
            t_aw = _positive(gas["adiabatic_wall_temperature_K"], "adiabatic wall temperature", segment_id)
            if gas.get("radiation_definition") != "EXPLICIT_NET_RATE_GAS_TO_WALL_POSITIVE":
                raise M0Stop("INPUT_U", "explicit net-radiation definition is required", segment_id=segment_id)
            q_rad = float(gas["q_rad_net_W"])

            t_in, p_in, h_in = temperature, pressure, enthalpy
            t_out, p_out = t_in, p_in
            converged = False
            for iteration in range(1, max_iterations + 1):
                mean_t = 0.5 * (t_in + t_out)
                mean_p = 0.5 * (p_in + p_out)
                state = table.state_pt(mean_p, mean_t)
                re = reynolds_number(mass_flow, flow_area, hydraulic_diameter, state.viscosity_Pa_s)
                pr = prandtl_number(state.cp_J_kgK, state.viscosity_Pa_s, state.conductivity_W_mK)
                factor = colebrook_darcy_friction_factor(re, roughness / hydraulic_diameter)
                nu, applicability = gnielinski_common_form(
                    re,
                    pr,
                    factor,
                    domain=domain,
                    geometry=str(geometry["closure_geometry"]),
                    fully_developed=bool(geometry["fully_developed"]),
                )
                if nu is None:
                    all_applicable = False
                    raise M0Stop(applicability.status, "; ".join(applicability.reasons), segment_id=segment_id)
                h_c = nu * state.conductivity_W_mK / hydraulic_diameter
                r_g = 1.0 / (h_g * gas_area)
                r_c = 1.0 / (surface_efficiency * h_c * coolant_area)
                q_total = (t_aw - mean_t + q_rad * r_g) / (r_g + wall_resistance + r_c)
                q_conv = q_total - q_rad
                twg = t_aw - q_conv * r_g
                twc = mean_t + q_total * r_c
                h_out = coolant_outlet_enthalpy_J_kg(h_in, q_total, mass_flow)
                candidate_state = table.state_ph(mean_p, h_out)
                velocity = mass_flow / (state.density_kg_m3 * flow_area)
                dp = darcy_weisbach_pressure_drop_Pa(
                    factor,
                    length,
                    hydraulic_diameter,
                    state.density_kg_m3,
                    velocity,
                    local_k,
                )
                candidate_p = p_in - dp
                if candidate_p <= 0:
                    raise M0Stop("OUTSIDE", "computed coolant pressure is non-positive", segment_id=segment_id)
                next_t = t_out + relaxation * (candidate_state.temperature_K - t_out)
                next_p = p_out + relaxation * (candidate_p - p_out)
                if abs(next_t - t_out) <= temperature_tolerance and abs(next_p - p_out) <= pressure_tolerance:
                    t_out, p_out = next_t, next_p
                    converged = True
                    break
                t_out, p_out = next_t, next_p
            if not converged:
                all_converged = False
                raise M0Stop("NUM_U", "segment iteration did not converge", segment_id=segment_id)

            outlet = table.state_ph(p_out, h_out)
            energy_residual = q_total - mass_flow * (h_out - h_in)
            wall_residual = wall_energy_residual_W(q_total, q_total, 0.0, 0.0, 0.0)
            if abs(energy_residual) > energy_tolerance or abs(wall_residual) > energy_tolerance:
                raise M0Stop("NUM_U", "segment energy residual exceeds tolerance", segment_id=segment_id)
            segment_results.append(
                {
                    "segment_id": segment_id,
                    "iterations": iteration,
                    "inlet": {"temperature_K": t_in, "pressure_Pa_abs": p_in, "enthalpy_J_kg": h_in},
                    "outlet": asdict(outlet),
                    "Re": re,
                    "Pr": pr,
                    "darcy_factor": factor,
                    "Nu": nu,
                    "h_c_W_m2K": h_c,
                    "q_conv_W": q_conv,
                    "q_rad_net_W": q_rad,
                    "q_total_W": q_total,
                    "T_wg_K": twg,
                    "T_wc_K": twc,
                    "pressure_drop_Pa": p_in - p_out,
                    "energy_residual_W": energy_residual,
                    "wall_residual_W": wall_residual,
                    "applicability": asdict(applicability),
                }
            )
            temperature, pressure, enthalpy = outlet.temperature_K, p_out, h_out
            total_heat += q_total
            min_pressure = min(min_pressure, p_out)

        max_wall = max(
            (value, item["segment_id"], side)
            for item in segment_results
            for value, side in ((item["T_wg_K"], "gas_side"), (item["T_wc_K"], "coolant_side"))
        )
        numerical_status = "PASS" if all_converged else "NUM_U"
        use_status = "MODEL_U"
        return {
            "run_id": run.get("run_id"),
            "status": use_status,
            "numerical_status": numerical_status,
            "model_scope": run["model_scope"],
            "segments": segment_results,
            "qoi": {
                "Y01": {"value": max_wall[0], "unit": "K", "location": max_wall[1], "surface": max_wall[2], "status": use_status},
                "Y02": {"value": start_pressure - pressure, "unit": "Pa", "status": use_status},
                "Y03": {"value": total_heat, "unit": "W", "status": use_status},
                "Y04": {"value": min_pressure, "unit": "Pa(a)", "status": use_status},
                "Y05": {"value": temperature, "unit": "K", "status": use_status},
            },
            "checks": {
                "closure_applicability": "PASS" if all_applicable else "OUTSIDE",
                "segment_convergence": numerical_status,
                "energy_residual_max_W": max(abs(item["energy_residual_W"]) for item in segment_results),
                "wall_residual_max_W": max(abs(item["wall_residual_W"]) for item in segment_results),
            },
            "implemented_models": [
                "steady single-path coolant energy",
                "three-resistance conjugate heat transfer with explicit net radiation",
                "tabulated temperature interpolation without extrapolation",
                "conditional Gnielinski displayed form",
                "Colebrook Darcy friction factor",
                "Darcy-Weisbach friction and local pressure loss",
            ],
            "not_implemented": [
                "pressure-driven flow solution",
                "parallel-channel distribution",
                "transient wall or fluid storage",
                "axial or multi-zone wall conduction",
                "boiling or two-phase flow",
                "coupled Bartz gas-side calculation inside the wall-temperature iteration",
                "uncertainty propagation and allowables assessment",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except M0Stop as error:
        return {
            "run_id": run.get("run_id"),
            "status": error.status,
            "stopped_at_segment": error.segment_id,
            "reason": str(error),
            "qoi": {f"Y{i:02d}": {"value": None, "status": error.status} for i in range(1, 6)},
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except (KeyError, TypeError, ValueError) as error:
        return {
            "run_id": run.get("run_id") if isinstance(run, dict) else None,
            "status": "INPUT_U",
            "stopped_at_segment": None,
            "reason": f"invalid or missing input: {error}",
            "qoi": {f"Y{i:02d}": {"value": None, "status": "INPUT_U"} for i in range(1, 6)},
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }


def solve_m0_file(path: str | Path, schema_path: str | Path | None = None) -> dict[str, Any]:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("M0 request must be a JSON object")
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
                return {
                    "run_id": request.get("run_id"),
                    "status": "INPUT_U",
                    "reason": "M0 request schema validation failed",
                    "schema_errors": [
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                    "qoi": {f"Y{i:02d}": {"value": None, "status": "INPUT_U"} for i in range(1, 6)},
                    "physical_validation_v01_v12": "NOT_RUN",
                    "validated_engine_prediction": False,
                }
    return solve_m0(request)
