"""Rounding tolerance: 0.05 by default, configurable."""

from decimal import Decimal

from invoice_agent.config import Config

from .conftest import codes, extraction


def test_default_tolerance_is_five_cents():
    assert Config().tolerance == Decimal("0.05")


def test_total_exactly_at_the_tolerance_passes():
    assert codes(extraction({"totals.total_printed": "270.30"})) == []   # +0.05
    assert codes(extraction({"totals.total_printed": "270.20"})) == []   # -0.05


def test_total_just_above_the_tolerance_fails():
    assert codes(extraction({"totals.total_printed": "270.31"})) == ["TOTAL_MISMATCH"]
    assert codes(extraction({"totals.total_printed": "270.19"})) == ["TOTAL_MISMATCH"]


def test_vat_rounding_within_tolerance():
    # 250.00 x 8.1 % = 20.25 exactly; printed 20.29 is inside 0.05
    ex = extraction({
        "totals.vat_breakdown": [{"rate": "8.1", "base": "250.00", "vat": "20.29"}],
        "totals.vat_total": "20.29", "totals.total_printed": "270.29",
    })
    assert codes(ex) == []


def test_lines_tolerance():
    assert codes(extraction({"totals.subtotal": "250.05", "totals.total_printed": "270.30"})) == []
    assert "LINES_MISMATCH" in codes(extraction({"totals.subtotal": "250.06", "totals.total_printed": "270.31"}))


def test_tolerance_can_be_changed():
    ex = extraction({"totals.total_printed": "270.33"})  # 0.08 off
    assert codes(ex) == ["TOTAL_MISMATCH"]
    assert codes(ex, tolerance="0.10") == []
    exact = extraction({"totals.total_printed": "270.26"})  # 0.01 off
    assert codes(exact, tolerance="0") == ["TOTAL_MISMATCH"]


def test_config_reads_tolerance_from_json_exactly():
    assert Config.model_validate({"tolerance": 0.1}).tolerance == Decimal("0.1")
