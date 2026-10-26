from datetime import date

import pytest

from invoice_agent.parsing import DateError, parse_date

from .conftest import codes, extraction


@pytest.mark.parametrize("text, expected", [
    ("2026-10-02", date(2026, 10, 2)),
    ("2026-1-5", date(2026, 1, 5)),
    ("2026-10-02T00:00:00", date(2026, 10, 2)),
    ("02.10.2026", date(2026, 10, 2)),
    ("2.10.26", date(2026, 10, 2)),
    ("02/10/2026", date(2026, 10, 2)),   # day first, as in Europe
    ("02-10-2026", date(2026, 10, 2)),
])
def test_parse_dates(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("empty", [None, "", "null"])
def test_no_date(empty):
    assert parse_date(empty) is None


@pytest.mark.parametrize("bad", ["2026-02-30", "31.04.2026", "next Monday", "2026/10", "13.13.2026"])
def test_invalid_dates_are_rejected(bad):
    with pytest.raises(DateError):
        parse_date(bad)


def test_missing_due_date():
    ex = extraction({"invoice.due_date": None})
    assert codes(ex) == ["MISSING_DUE_DATE"]


def test_due_date_before_invoice_date():
    ex = extraction({"invoice.date": "2026-03-02", "invoice.due_date": "2026-02-28"})
    assert codes(ex) == ["DUE_BEFORE_DATE"]


def test_due_date_on_the_invoice_date_is_fine():
    ex = extraction({"invoice.date": "2026-03-02", "invoice.due_date": "2026-03-02"})
    assert codes(ex) == []


def test_missing_invoice_date_is_a_missing_field():
    ex = extraction({"invoice.date": None})
    assert codes(ex) == ["MISSING_FIELD"]
