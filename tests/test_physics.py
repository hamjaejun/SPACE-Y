from __future__ import annotations

import unittest

from space_y.physics import (
    CorrelationDomain,
    colebrook_darcy_friction_factor,
    colebrook_residual,
    coolant_outlet_enthalpy_J_kg,
    gnielinski_common_form,
    run_basic_checks,
)


class PhysicsTests(unittest.TestCase):
    def test_basic_checks(self) -> None:
        self.assertEqual(run_basic_checks()["status"], "PASS")

    def test_nonpositive_mass_flow_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            coolant_outlet_enthalpy_J_kg(1.0, 1.0, 0.0)

    def test_colebrook_residual(self) -> None:
        factor = colebrook_darcy_friction_factor(100_000.0, 1e-4)
        self.assertLess(abs(colebrook_residual(factor, 100_000.0, 1e-4)), 1e-9)

    def test_gnielinski_stops_without_domain(self) -> None:
        value, applicability = gnielinski_common_form(
            100_000.0,
            1.2,
            0.02,
            domain=None,
            geometry="circular_tube",
            fully_developed=True,
        )
        self.assertIsNone(value)
        self.assertEqual(applicability.status, "INPUT_U")

    def test_gnielinski_rejects_outside_supplied_domain(self) -> None:
        domain = CorrelationDomain(
            source="unit-test fixture only",
            re_min=10_000.0,
            re_max=20_000.0,
            pr_min=1.0,
            pr_max=2.0,
            geometry="circular_tube",
            fully_developed_required=True,
        )
        value, applicability = gnielinski_common_form(
            30_000.0,
            1.2,
            0.02,
            domain=domain,
            geometry="circular_tube",
            fully_developed=True,
        )
        self.assertIsNone(value)
        self.assertEqual(applicability.status, "OUTSIDE")


if __name__ == "__main__":
    unittest.main()
