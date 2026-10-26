from decimal import Decimal

from invoice_agent.checks import is_swiss_invoice, run_checks

from .conftest import SWISS, codes, extraction


def test_correct_invoice_has_no_code():
    assert codes(extraction()) == []


def test_old_swiss_rate_is_flagged():
    ex = extraction({
        "line_items.0.vat_rate": "7.7", "line_items.1.vat_rate": "7.7",
        "totals.vat_breakdown": [{"rate": "7.7", "base": "250.00", "vat": "19.25"}],
        "totals.vat_total": "19.25", "totals.total_printed": "269.25",
    })
    assert codes(ex) == ["VAT_RATE_INVALID"]


def test_every_current_swiss_rate_is_accepted():
    for rate, vat in (("8.1", "20.25"), ("2.6", "6.50"), ("3.8", "9.50"), ("0", "0.00")):
        total = str(Decimal("250.00") + Decimal(vat))
        ex = extraction({
            "line_items.0.vat_rate": rate, "line_items.1.vat_rate": rate,
            "totals.vat_breakdown": [{"rate": rate, "base": "250.00", "vat": vat}],
            "totals.vat_total": vat, "totals.total_printed": total,
        })
        assert codes(ex) == [], rate


def test_rate_list_comes_from_the_configuration():
    ex = extraction({
        "line_items.0.vat_rate": "7.7", "line_items.1.vat_rate": "7.7",
        "totals.vat_breakdown": [{"rate": "7.7", "base": "250.00", "vat": "19.25"}],
        "totals.vat_total": "19.25", "totals.total_printed": "269.25",
    })
    old_rates = [Decimal("7.7"), Decimal("2.5"), Decimal("3.7"), Decimal("0")]
    assert codes(ex, rates=old_rates) == []
    assert codes(extraction(), rates=old_rates) == ["VAT_RATE_INVALID"]


def test_foreign_invoice_is_not_checked_against_swiss_rates():
    ex = extraction({
        "supplier.vat_id": "DE123456789",
        "invoice.currency": "EUR",
        "payment.iban": "DE89 3704 0044 0532 0130 00",
        "line_items.0.vat_rate": "19", "line_items.1.vat_rate": "19",
        "totals.vat_breakdown": [{"rate": "19", "base": "250.00", "vat": "47.50"}],
        "totals.vat_total": "47.50", "totals.total_printed": "297.50",
    })
    assert not is_swiss_invoice(ex)
    assert codes(ex) == []


def test_reverse_charge_zero_rate_on_a_foreign_invoice():
    ex = extraction({
        "supplier.vat_id": "IE 1234567T", "invoice.currency": "EUR",
        "payment.iban": "IE29 AIBK 9311 5212 3456 78",
        "line_items.0.vat_rate": "0", "line_items.1.vat_rate": "0",
        "totals.vat_breakdown": [{"rate": "0", "base": None, "vat": "0.00"}],
        "totals.vat_total": "0.00", "totals.total_printed": "250.00",
    })
    assert codes(ex) == []


def test_swiss_detection():
    assert is_swiss_invoice(extraction())  # CHE UID
    no_vat_id = extraction({"supplier.vat_id": None, "invoice.currency": "EUR"})
    assert is_swiss_invoice(no_vat_id)  # Swiss IBAN
    no_vat_id_foreign_iban = extraction({
        "supplier.vat_id": None, "invoice.currency": "CHF", "payment.iban": "DE89 3704 0044 0532 0130 00",
    })
    assert is_swiss_invoice(no_vat_id_foreign_iban)  # CHF
    foreign = extraction({"supplier.vat_id": "FR40303265045", "invoice.currency": "CHF"})
    assert not is_swiss_invoice(foreign)


def test_vat_amount_must_match_base_times_rate():
    ex = extraction({
        "totals.vat_breakdown": [{"rate": "8.1", "base": "250.00", "vat": "25.00"}],
        "totals.vat_total": "25.00", "totals.total_printed": "275.00",
    })
    assert codes(ex) == ["VAT_MISMATCH"]


def test_two_rates_are_checked_separately():
    good = extraction({
        "line_items.1.vat_rate": "2.6",
        "totals.vat_breakdown": [
            {"rate": "8.1", "base": "200.00", "vat": "16.20"},
            {"rate": "2.6", "base": "50.00", "vat": "1.30"},
        ],
        "totals.vat_total": "17.50", "totals.total_printed": "267.50",
    })
    assert codes(good) == []
    wrong_second_rate = extraction({
        "line_items.1.vat_rate": "2.6",
        "totals.vat_breakdown": [
            {"rate": "8.1", "base": "200.00", "vat": "16.20"},
            {"rate": "2.6", "base": "50.00", "vat": "4.05"},
        ],
        "totals.vat_total": "20.25", "totals.total_printed": "270.25",
    })
    assert codes(wrong_second_rate) == ["VAT_MISMATCH"]


def test_vat_parts_must_add_up_to_the_vat_total():
    ex = extraction({
        "line_items.1.vat_rate": "2.6",
        "totals.vat_breakdown": [
            {"rate": "8.1", "base": "200.00", "vat": "16.20"},
            {"rate": "2.6", "base": "50.00", "vat": "1.30"},
        ],
        "totals.vat_total": "18.50", "totals.total_printed": "268.50",
    })
    assert "VAT_MISMATCH" in codes(ex)


def test_missing_base_is_taken_from_the_lines_with_that_rate():
    ex = extraction({
        "line_items.1.vat_rate": "2.6",
        "totals.vat_breakdown": [
            {"rate": "8.1", "base": None, "vat": "16.20"},
            {"rate": "2.6", "base": None, "vat": "1.30"},
        ],
        "totals.vat_total": "17.50", "totals.total_printed": "267.50",
    })
    result = run_checks(ex, tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert result.codes == []
    assert [row["base"] for row in result.vat_table] == [Decimal("200.00"), Decimal("50.00")]


def test_without_vat_summary_the_lines_are_grouped_by_rate():
    ex = extraction({"totals.vat_breakdown": []})
    result = run_checks(ex, tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert result.codes == []
    assert result.vat_table[0]["base"] == Decimal("250.00")
    wrong = extraction({"totals.vat_breakdown": [], "totals.vat_total": "30.00", "totals.total_printed": "280.00"})
    assert codes(wrong) == ["VAT_MISMATCH"]


def test_vat_total_is_the_sum_of_parts_when_not_printed():
    ex = extraction({
        "line_items.1.vat_rate": "2.6",
        "totals.vat_breakdown": [
            {"rate": "8.1", "base": "200.00", "vat": "16.20"},
            {"rate": "2.6", "base": "50.00", "vat": "1.30"},
        ],
        "totals.vat_total": None, "totals.total_printed": "267.50",
    })
    result = run_checks(ex, tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert result.codes == []
    assert result.vat_used == Decimal("17.50")
