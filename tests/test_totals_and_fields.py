from decimal import Decimal

from invoice_agent.checks import CODES, NEEDS_REVIEW, OK, run_checks

from invoice_agent.models import InvoiceExtraction

from .conftest import SWISS, codes, extraction, payload


def test_status_is_ok_only_without_codes():
    assert run_checks(extraction(), tolerance=Decimal("0.05"), swiss_rates=SWISS).status == OK
    bad = run_checks(extraction({"totals.total_printed": "279.25"}), tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert bad.status == NEEDS_REVIEW


def test_wrong_printed_total():
    result = run_checks(extraction({"totals.total_printed": "279.25"}), tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert result.codes == ["TOTAL_MISMATCH"]
    assert result.computed_total == Decimal("270.25")
    assert "9.00" in result.issues[0].message


def test_lines_do_not_add_up():
    ex = extraction({"line_items.1.amount": "60.00"})
    assert codes(ex) == ["LINES_MISMATCH"]


def test_line_without_amount():
    ex = extraction({"line_items.1.amount": None})
    assert codes(ex) == ["LINES_MISMATCH"]


def test_no_line_items():
    ex = extraction({"line_items": []})
    assert codes(ex) == ["LINES_MISMATCH"]


def test_amounts_in_any_format_are_checked_the_same_way():
    ex = extraction({
        "line_items.0.unit_price": "1'000.00", "line_items.0.amount": "2'000.00",
        "totals.subtotal": "2 050,00",
        "totals.vat_breakdown": [{"rate": "8,1", "base": "2,050.00", "vat": "166.05"}],
        "totals.vat_total": "166.05", "totals.total_printed": "2.216,05",
    })
    assert codes(ex) == []


def test_missing_fields_are_listed():
    ex = extraction({"supplier.name": None, "invoice.number": None})
    result = run_checks(ex, tolerance=Decimal("0.05"), swiss_rates=SWISS)
    assert result.codes == ["MISSING_FIELD"]
    assert "supplier" in result.issues[0].message and "invoice number" in result.issues[0].message


def test_missing_total():
    assert codes(extraction({"totals.total_printed": None})) == ["MISSING_FIELD"]


def test_codes_are_reported_in_a_stable_order():
    ex = extraction({"payment.iban": "CH00 0000 0000 0000 0000 0", "invoice.due_date": None,
                     "totals.total_printed": "300.00"})
    found = codes(ex)
    assert found == sorted(found, key=CODES.index)
    assert found == ["TOTAL_MISMATCH", "MISSING_DUE_DATE", "IBAN_INVALID"]


def test_line_without_amount_is_flagged_even_without_subtotal():
    ex = extraction({"line_items.1.amount": None, "totals.subtotal": None})
    assert codes(ex) == ["LINES_MISMATCH"]


def test_without_printed_subtotal_the_lines_are_used_for_the_total():
    assert codes(extraction({"totals.subtotal": None})) == []
    assert codes(extraction({"totals.subtotal": None, "totals.total_printed": "300.00"})) == ["TOTAL_MISMATCH"]


def test_information_lines_without_amounts_are_ignored():
    data = payload()
    data["line_items"].append({"description": "Lieferschein 4711 vom 01.03.", "quantity": None,
                               "unit_price": None, "vat_rate": None, "amount": None})
    assert codes(InvoiceExtraction.from_ai(data)) == []


def test_only_information_lines_is_flagged():
    data = payload()
    data["line_items"] = [{"description": "Siehe Beilage", "quantity": None, "unit_price": None,
                           "vat_rate": None, "amount": None}]
    assert codes(InvoiceExtraction.from_ai(data)) == ["LINES_MISMATCH"]


def test_absurd_amounts_never_crash_the_report():
    from invoice_agent.checks import money

    assert money(Decimal("1E+40")) == "1E+40"


def test_vat_inclusive_prices_end_up_in_review():
    # Prices include VAT ("of which VAT 8.1 %: 8.10"), no net subtotal printed.
    ex = extraction({
        "line_items": [{"description": "Item", "quantity": "1", "unit_price": "108.10", "vat_rate": "8.1",
                        "amount": "108.10"}],
        "totals.subtotal": None,
        "totals.vat_breakdown": [{"rate": "8.1", "base": None, "vat": "8.10"}],
        "totals.vat_total": "8.10", "totals.total_printed": "108.10",
    })
    assert codes(ex) == ["VAT_MISMATCH", "TOTAL_MISMATCH"]
