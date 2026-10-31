from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Protocol, runtime_checkable


STATUS_INPUT_U = "INPUT_U"
STATUS_OUTSIDE = "OUTSIDE"
STATUS_MODEL_U = "MODEL_U"
SUPPORT_AVAILABLE = "AVAILABLE"
SUPPORT_INPUT_U = "INPUT_U"

CAPABILITY_PT = "state_pt"
CAPABILITY_PH = "state_ph"
CAPABILITY_DENSITY = "density"
CAPABILITY_VISCOSITY = "viscosity"
CAPABILITY_CP = "cp"
CAPABILITY_CONDUCTIVITY = "conductivity"
CAPABILITY_SATURATION_T = "saturation_at_temperature"
CAPABILITY_SATURATION_P = "saturation_at_pressure"
CAPABILITY_PHASE_PT = "phase_pt"
CAPABILITY_PHASE_PH = "phase_ph"

REQUIRED_M0_CAPABILITIES = (
    CAPABILITY_PT,
    CAPABILITY_PH,
    CAPABILITY_DENSITY,
    CAPABILITY_VISCOSITY,
    CAPABILITY_CP,
    CAPABILITY_CONDUCTIVITY,
    CAPABILITY_SATURATION_T,
    CAPABILITY_SATURATION_P,
    CAPABILITY_PHASE_PT,
    CAPABILITY_PHASE_PH,
)


class PropertyAdapterStop(RuntimeError):
    """Typed stop used at the property boundary; it never returns partial values."""

    def __init__(self, status: str, message: str, *, capability: str):
        if status not in {STATUS_INPUT_U, STATUS_OUTSIDE, STATUS_MODEL_U}:
            raise ValueError(f"invalid property stop status: {status}")
        super().__init__(message)
        self.status = status
        self.capability = capability


@dataclass(frozen=True)
class Capability:
    name: str
    status: str
    evidence: str
    valid_temperature_range_K: tuple[float, float] | None = None
    valid_pressure_range_Pa_abs: tuple[float, float] | None = None
    phase_scope: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {SUPPORT_AVAILABLE, SUPPORT_INPUT_U}:
            raise ValueError(f"invalid capability status for {self.name}: {self.status}")


@dataclass(frozen=True)
class PropertyProvenance:
    fluid_name: str
    cas_number: str | None
    provider: str
    dataset_or_model: str
    version: str
    source_urls: tuple[str, ...]
    license_summary: str
    redistribution_scope: str
    enthalpy_reference: str
    unit_system: str = "SI: Pa(abs), K, J/kg, kg/m3, Pa*s, J/(kg*K), W/(m*K)"
    use_scope: str = "UNSPECIFIED"


@dataclass(frozen=True)
class PropertyState:
    temperature_K: float
    pressure_Pa_abs: float
    enthalpy_J_kg: float
    density_kg_m3: float
    viscosity_Pa_s: float
    cp_J_kgK: float
    conductivity_W_mK: float
    enthalpy_reference: str


@dataclass(frozen=True)
class SaturationState:
    temperature_K: float
    pressure_Pa_abs: float
    liquid_enthalpy_J_kg: float | None
    vapor_enthalpy_J_kg: float | None
    enthalpy_reference: str


@dataclass(frozen=True)
class PhaseAssessment:
    phase: str
    evidence: str
    single_phase_confirmed: bool


@runtime_checkable
class PropertyAdapter(Protocol):
    provenance: PropertyProvenance

    def capability_report(self) -> dict[str, Capability]: ...

    def state_pt(self, pressure_Pa_abs: float, temperature_K: float) -> PropertyState: ...

    def state_ph(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> PropertyState: ...

    def density_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float: ...

    def viscosity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float: ...

    def cp_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float: ...

    def conductivity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float: ...

    def saturation_at_temperature(self, temperature_K: float) -> SaturationState: ...

    def saturation_at_pressure(self, pressure_Pa_abs: float) -> SaturationState: ...

    def classify_phase_pt(
        self, pressure_Pa_abs: float, temperature_K: float
    ) -> PhaseAssessment: ...

    def classify_phase_ph(
        self, pressure_Pa_abs: float, enthalpy_J_kg: float
    ) -> PhaseAssessment: ...


def _finite(value: Any, name: str, capability: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise PropertyAdapterStop(
            STATUS_INPUT_U, f"{name} must be a finite number", capability=capability
        ) from error
    if not math.isfinite(number):
        raise PropertyAdapterStop(
            STATUS_INPUT_U, f"{name} must be a finite number", capability=capability
        )
    return number


def _positive(value: Any, name: str, capability: str) -> float:
    number = _finite(value, name, capability)
    if number <= 0:
        raise PropertyAdapterStop(
            STATUS_INPUT_U, f"{name} must be positive", capability=capability
        )
    return number


def require_exact_ipa_identity(fluid_name: str, cas_number: str) -> None:
    """Reject aliases that could silently select 1-propanol or generic propanol."""

    normalized = fluid_name.strip().casefold().replace("_", "-")
    accepted = {"2-propanol", "propan-2-ol", "isopropanol", "isopropyl alcohol"}
    if normalized not in accepted or cas_number.strip() != "67-63-0":
        raise PropertyAdapterStop(
            STATUS_INPUT_U,
            "property identity must explicitly resolve to 2-propanol, CAS 67-63-0",
            capability="fluid_identity",
        )


class SyntheticTabulatedPropertyAdapter:
    """Bounded interpolation for arithmetic tests, never an IPA data model."""

    def __init__(self, spec: dict[str, Any]):
        if spec.get("purpose") != "SYNTHETIC_VERIFICATION_ONLY":
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "synthetic adapter requires purpose=SYNTHETIC_VERIFICATION_ONLY",
                capability="provider_initialization",
            )
        if spec.get("model") != "TABULATED_T_PRESSURE_BOUNDED":
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "unsupported or missing synthetic property model",
                capability="provider_initialization",
            )
        source = str(spec.get("source") or "").strip()
        version = str(spec.get("version") or "").strip()
        reference = str(spec.get("enthalpy_reference") or "").strip()
        if not source or not version or not reference:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "source, version, and enthalpy_reference are required",
                capability="provider_initialization",
            )
        try:
            p_range = tuple(float(value) for value in spec["pressure_range_Pa_abs"])
            rows = sorted(spec["rows"], key=lambda row: float(row["temperature_K"]))
        except (KeyError, TypeError, ValueError) as error:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                f"invalid synthetic property table: {error}",
                capability="provider_initialization",
            ) from error
        if len(p_range) != 2 or p_range[0] <= 0 or p_range[1] <= p_range[0]:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "pressure_range_Pa_abs must be a positive increasing pair",
                capability="provider_initialization",
            )
        if len(rows) < 2:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "synthetic property table needs at least two rows",
                capability="provider_initialization",
            )
        temperatures = [_finite(row.get("temperature_K"), "temperature_K", CAPABILITY_PT) for row in rows]
        enthalpies = [_finite(row.get("enthalpy_J_kg"), "enthalpy_J_kg", CAPABILITY_PH) for row in rows]
        if any(right <= left for left, right in zip(temperatures, temperatures[1:])):
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "synthetic table temperatures must strictly increase",
                capability="provider_initialization",
            )
        if any(right <= left for left, right in zip(enthalpies, enthalpies[1:])):
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "synthetic table enthalpies must strictly increase",
                capability="provider_initialization",
            )
        self._p_range = (p_range[0], p_range[1])
        self._rows = rows
        self._t_range = (temperatures[0], temperatures[-1])
        self._h_range = (enthalpies[0], enthalpies[-1])
        self.provenance = PropertyProvenance(
            fluid_name="SYNTHETIC_FLUID_NOT_IPA",
            cas_number=None,
            provider="SPACE-Y",
            dataset_or_model="bounded linear arithmetic fixture",
            version=version,
            source_urls=(),
            license_summary="project test fixture",
            redistribution_scope="repository test data only",
            enthalpy_reference=reference,
            use_scope="SYNTHETIC_VERIFICATION_ONLY_NOT_ENGINE_PREDICTION",
        )

    def capability_report(self) -> dict[str, Capability]:
        available = {
            name: Capability(
                name,
                SUPPORT_AVAILABLE,
                "synthetic bounded table; arithmetic verification only",
                self._t_range,
                self._p_range,
                "DECLARED_SINGLE_PHASE_TEST_DOMAIN",
            )
            for name in (
                CAPABILITY_PT,
                CAPABILITY_PH,
                CAPABILITY_DENSITY,
                CAPABILITY_VISCOSITY,
                CAPABILITY_CP,
                CAPABILITY_CONDUCTIVITY,
            )
        }
        for name in (CAPABILITY_SATURATION_T, CAPABILITY_SATURATION_P):
            available[name] = Capability(
                name,
                SUPPORT_INPUT_U,
                "no saturation or coexistence model in synthetic table",
            )
        available[CAPABILITY_PHASE_PT] = Capability(
            CAPABILITY_PHASE_PT,
            SUPPORT_AVAILABLE,
            "only confirms membership in the declared synthetic single-phase domain",
            self._t_range,
            self._p_range,
            "DECLARED_SINGLE_PHASE_TEST_DOMAIN",
        )
        available[CAPABILITY_PHASE_PH] = Capability(
            CAPABILITY_PHASE_PH,
            SUPPORT_AVAILABLE,
            "synthetic p,h inversion followed by declared test-domain check",
            self._t_range,
            self._p_range,
            "DECLARED_SINGLE_PHASE_TEST_DOMAIN",
        )
        return available

    def _pressure_check(self, pressure_Pa_abs: float, capability: str) -> float:
        pressure = _positive(pressure_Pa_abs, "pressure_Pa_abs", capability)
        if not self._p_range[0] <= pressure <= self._p_range[1]:
            raise PropertyAdapterStop(
                STATUS_OUTSIDE,
                "pressure is outside the supplied synthetic property range",
                capability=capability,
            )
        return pressure

    def _bracket(
        self, key: str, value: float, capability: str
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        values = [float(row[key]) for row in self._rows]
        if not values[0] <= value <= values[-1]:
            raise PropertyAdapterStop(
                STATUS_OUTSIDE,
                f"{key} is outside the supplied synthetic property range",
                capability=capability,
            )
        for left, right in zip(self._rows, self._rows[1:]):
            lower, upper = float(left[key]), float(right[key])
            if lower <= value <= upper:
                return left, right, (value - lower) / (upper - lower)
        return self._rows[-2], self._rows[-1], 1.0

    @staticmethod
    def _lerp(left: dict[str, Any], right: dict[str, Any], fraction: float, key: str) -> float:
        return float(left[key]) + fraction * (float(right[key]) - float(left[key]))

    def state_pt(self, pressure_Pa_abs: float, temperature_K: float) -> PropertyState:
        pressure = self._pressure_check(pressure_Pa_abs, CAPABILITY_PT)
        temperature = _positive(temperature_K, "temperature_K", CAPABILITY_PT)
        left, right, fraction = self._bracket("temperature_K", temperature, CAPABILITY_PT)
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
        for key in (
            "density_kg_m3",
            "viscosity_Pa_s",
            "cp_J_kgK",
            "conductivity_W_mK",
        ):
            _positive(values[key], key, CAPABILITY_PT)
        return PropertyState(
            temperature,
            pressure,
            values["enthalpy_J_kg"],
            values["density_kg_m3"],
            values["viscosity_Pa_s"],
            values["cp_J_kgK"],
            values["conductivity_W_mK"],
            self.provenance.enthalpy_reference,
        )

    def state_ph(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> PropertyState:
        pressure = self._pressure_check(pressure_Pa_abs, CAPABILITY_PH)
        enthalpy = _finite(enthalpy_J_kg, "enthalpy_J_kg", CAPABILITY_PH)
        left, right, fraction = self._bracket("enthalpy_J_kg", enthalpy, CAPABILITY_PH)
        temperature = self._lerp(left, right, fraction, "temperature_K")
        state = self.state_pt(pressure, temperature)
        if state.enthalpy_reference != self.provenance.enthalpy_reference:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                "enthalpy reference changed during p,h inversion",
                capability=CAPABILITY_PH,
            )
        return state

    def density_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        return self.state_pt(pressure_Pa_abs, temperature_K).density_kg_m3

    def viscosity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        return self.state_pt(pressure_Pa_abs, temperature_K).viscosity_Pa_s

    def cp_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        return self.state_pt(pressure_Pa_abs, temperature_K).cp_J_kgK

    def conductivity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        return self.state_pt(pressure_Pa_abs, temperature_K).conductivity_W_mK

    def saturation_at_temperature(self, temperature_K: float) -> SaturationState:
        _positive(temperature_K, "temperature_K", CAPABILITY_SATURATION_T)
        raise PropertyAdapterStop(
            STATUS_INPUT_U,
            "synthetic table has no saturation model",
            capability=CAPABILITY_SATURATION_T,
        )

    def saturation_at_pressure(self, pressure_Pa_abs: float) -> SaturationState:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_SATURATION_P)
        raise PropertyAdapterStop(
            STATUS_INPUT_U,
            "synthetic table has no saturation model",
            capability=CAPABILITY_SATURATION_P,
        )

    def classify_phase_pt(
        self, pressure_Pa_abs: float, temperature_K: float
    ) -> PhaseAssessment:
        self._pressure_check(pressure_Pa_abs, CAPABILITY_PHASE_PT)
        temperature = _positive(temperature_K, "temperature_K", CAPABILITY_PHASE_PT)
        if not self._t_range[0] <= temperature <= self._t_range[1]:
            raise PropertyAdapterStop(
                STATUS_OUTSIDE,
                "temperature is outside the declared synthetic phase domain",
                capability=CAPABILITY_PHASE_PT,
            )
        return PhaseAssessment(
            "DECLARED_SINGLE_PHASE_TEST_DOMAIN",
            "fixture declaration only; no saturation or phase-equilibrium evidence",
            True,
        )

    def classify_phase_ph(
        self, pressure_Pa_abs: float, enthalpy_J_kg: float
    ) -> PhaseAssessment:
        state = self.state_ph(pressure_Pa_abs, enthalpy_J_kg)
        assessment = self.classify_phase_pt(state.pressure_Pa_abs, state.temperature_K)
        return PhaseAssessment(
            assessment.phase,
            "fixture p,h inversion followed by declared-domain check; no equilibrium evidence",
            assessment.single_phase_confirmed,
        )

    def trace_record(self) -> dict[str, Any]:
        return {
            "provenance": asdict(self.provenance),
            "capabilities": {
                name: asdict(capability)
                for name, capability in self.capability_report().items()
            },
        }


class ReviewedIPAReadinessAdapter:
    """Metadata-only IPA gate until a reviewed provider configuration exists."""

    def __init__(
        self,
        provenance: PropertyProvenance,
        capabilities: dict[str, Capability],
    ):
        require_exact_ipa_identity(provenance.fluid_name, provenance.cas_number or "")
        missing = set(REQUIRED_M0_CAPABILITIES) - set(capabilities)
        if missing:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                f"capability declarations missing: {sorted(missing)}",
                capability="provider_initialization",
            )
        self.provenance = provenance
        self._capabilities = dict(capabilities)

    def capability_report(self) -> dict[str, Capability]:
        return dict(self._capabilities)

    def _blocked(self, capability: str) -> None:
        declaration = self._capabilities[capability]
        if declaration.status != SUPPORT_AVAILABLE:
            raise PropertyAdapterStop(
                STATUS_INPUT_U,
                declaration.evidence,
                capability=capability,
            )
        raise PropertyAdapterStop(
            STATUS_MODEL_U,
            "capability is declared but no executable backend is bound",
            capability=capability,
        )

    def state_pt(self, pressure_Pa_abs: float, temperature_K: float) -> PropertyState:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_PT)
        _positive(temperature_K, "temperature_K", CAPABILITY_PT)
        self._blocked(CAPABILITY_PT)

    def state_ph(self, pressure_Pa_abs: float, enthalpy_J_kg: float) -> PropertyState:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_PH)
        _finite(enthalpy_J_kg, "enthalpy_J_kg", CAPABILITY_PH)
        self._blocked(CAPABILITY_PH)

    def density_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_DENSITY)
        _positive(temperature_K, "temperature_K", CAPABILITY_DENSITY)
        self._blocked(CAPABILITY_DENSITY)

    def viscosity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_VISCOSITY)
        _positive(temperature_K, "temperature_K", CAPABILITY_VISCOSITY)
        self._blocked(CAPABILITY_VISCOSITY)

    def cp_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_CP)
        _positive(temperature_K, "temperature_K", CAPABILITY_CP)
        self._blocked(CAPABILITY_CP)

    def conductivity_pt(self, pressure_Pa_abs: float, temperature_K: float) -> float:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_CONDUCTIVITY)
        _positive(temperature_K, "temperature_K", CAPABILITY_CONDUCTIVITY)
        self._blocked(CAPABILITY_CONDUCTIVITY)

    def saturation_at_temperature(self, temperature_K: float) -> SaturationState:
        _positive(temperature_K, "temperature_K", CAPABILITY_SATURATION_T)
        self._blocked(CAPABILITY_SATURATION_T)

    def saturation_at_pressure(self, pressure_Pa_abs: float) -> SaturationState:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_SATURATION_P)
        self._blocked(CAPABILITY_SATURATION_P)

    def classify_phase_pt(
        self, pressure_Pa_abs: float, temperature_K: float
    ) -> PhaseAssessment:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_PHASE_PT)
        _positive(temperature_K, "temperature_K", CAPABILITY_PHASE_PT)
        self._blocked(CAPABILITY_PHASE_PT)

    def classify_phase_ph(
        self, pressure_Pa_abs: float, enthalpy_J_kg: float
    ) -> PhaseAssessment:
        _positive(pressure_Pa_abs, "pressure_Pa_abs", CAPABILITY_PHASE_PH)
        _finite(enthalpy_J_kg, "enthalpy_J_kg", CAPABILITY_PHASE_PH)
        self._blocked(CAPABILITY_PHASE_PH)


def readiness_adapter_from_record(record: dict[str, Any]) -> ReviewedIPAReadinessAdapter:
    identity = record.get("fluid_identity", {})
    provenance_record = record.get("provenance", {})
    enthalpy_reference = provenance_record.get("enthalpy_reference")
    if not enthalpy_reference:
        enthalpy_reference = "UNCONFIRMED"
    provenance = PropertyProvenance(
        fluid_name=str(identity.get("chemical_name") or ""),
        cas_number=str(identity.get("cas_number") or ""),
        provider=str(provenance_record.get("provider") or "UNSELECTED"),
        dataset_or_model=str(provenance_record.get("dataset_or_model") or "UNSELECTED"),
        version=str(provenance_record.get("version") or "UNCONFIRMED"),
        source_urls=tuple(provenance_record.get("source_urls") or ()),
        license_summary=str(provenance_record.get("license_summary") or "UNCONFIRMED"),
        redistribution_scope=str(
            provenance_record.get("redistribution_scope") or "REVIEW_REQUIRED"
        ),
        enthalpy_reference=str(enthalpy_reference),
        use_scope="ACTUAL_IPA_READINESS_ONLY_NO_ENGINE_RUN",
    )
    capabilities: dict[str, Capability] = {}
    declarations = record.get("capabilities", {})
    for name in REQUIRED_M0_CAPABILITIES:
        declaration = declarations.get(name, {})
        capabilities[name] = Capability(
            name=name,
            status=str(declaration.get("status") or SUPPORT_INPUT_U),
            evidence=str(declaration.get("evidence") or "no reviewed evidence supplied"),
            valid_temperature_range_K=(
                tuple(float(v) for v in declaration["valid_temperature_range_K"])
                if declaration.get("valid_temperature_range_K") is not None
                else None
            ),
            valid_pressure_range_Pa_abs=(
                tuple(float(v) for v in declaration["valid_pressure_range_Pa_abs"])
                if declaration.get("valid_pressure_range_Pa_abs") is not None
                else None
            ),
            phase_scope=declaration.get("phase_scope"),
        )
    return ReviewedIPAReadinessAdapter(provenance, capabilities)
