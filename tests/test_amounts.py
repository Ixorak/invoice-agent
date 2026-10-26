from decimal import Decimal

import pytest

from invoice_agent.parsing import AmountError, infer_decimal_separator, parse_amount, parse_quantity, parse_rate

D = Decimal


@pytest.mark.parametrize("text", [
    "1'245.80",          # Swiss
    "1’245.80",     # Swiss, typographic apostrophe
    "1 245,80",          # French, space
    "1 245,80",     # French, no-break space
    "1 245,80",     # French, narrow no-break space
    "1,245.80",          # English
    "1.245,80",          # German
    "1245.80",
    "1245,80",
    "CHF 1'245.80",
    "1'245.80 CHF",
    "EUR 1 245,80",
    "€1,245.80",
])
def test_the_three_formats_and_their_variants(text):
    assert parse_amount(text) == D("1245.80")


@pytest.mark.parametrize("text, expected", [
    ("0.50", D("0.50")),
    ("12", D("12")),
    ("1'000'000.00", D("1000000.00")),
    ("1 000 000,00", D("1000000.00")),
    ("1,000,000.00", D("1000000.00")),
    ("1.000.000", D("1000000")),
    ("120.-", D("120")),            # Swiss "120.-"
    ("120.–", D("120")),
    ("-12.50", D("-12.50")),
    ("(12.50)", D("-12.50")),
    ("12.50-", D("-12.50")),
    (".5", D("0.5")),
])
def test_other_common_writings(text, expected):
    assert parse_amount(text) == expected


def test_numbers_pass_through_exactly():
    assert parse_amount(39.37) == D("39.37")
    assert parse_amount(1200) == D("1200")
    assert parse_amount(D("8.10")) == D("8.10")


@pytest.mark.parametrize("empty", [None, "", "  ", "null", "n/a", "-"])
def test_empty_values_are_none(empty):
    assert parse_amount(empty) is None


@pytest.mark.parametrize("bad", ["abc", "12a", "1,2,3.4.5", "1.24.5", "12..5", True, [], "1'24.80"])
def test_garbage_is_rejected(bad):
    with pytest.raises(AmountError):
        parse_amount(bad)


def test_ambiguous_three_decimals_uses_the_invoice_convention():
    # "1,245" alone: read as a thousands separator ...
    assert parse_amount("1,245") == D("1245")
    # ... unless the invoice uses "," as decimal mark elsewhere
    assert parse_amount("1,245", decimal_separator=",") == D("1.245")
    assert parse_amount("1.245", decimal_separator=".") == D("1.245")
    # a leading zero is never a thousands group
    assert parse_amount("0,850") == D("0.850")


def test_decimal_mark_is_inferred_from_unambiguous_amounts():
    assert infer_decimal_separator(["1'094.00", "88.61", "8.1"]) == "."
    assert infer_decimal_separator(["1 245,80", "12,50"]) == ","
    assert infer_decimal_separator(["1,245.80"]) == "."
    assert infer_decimal_separator(["1.245,80"]) == ","
    assert infer_decimal_separator(["12", "1,245"]) is None


def test_rates_and_quantities():
    assert parse_rate("8.1 %") == D("8.1")
    assert parse_rate("8,1%") == D("8.1")
    assert parse_rate("0") == D("0")
    assert parse_rate(7.7) == D("7.7")
    with pytest.raises(AmountError):
        parse_rate("150")
    assert parse_quantity("2.5 h") == D("2.5")
    assert parse_quantity("40 kg") == D("40")
    assert parse_quantity("3x") == D("3")
    assert parse_quantity("1,5 Std.") == D("1.5")


def test_implausibly_large_amounts_are_rejected():
    assert parse_amount("999'999'999'999.99") == D("999999999999.99")
    for huge in ("1234567890123456.00", "1" + "0" * 27, 10 ** 20, 1e20):
        with pytest.raises(AmountError):
            parse_amount(huge)
