import pytest

from invoice_agent.iban import format_iban, iban_problem, is_valid_iban, mod97
from invoice_agent.parsing import normalize_iban

from .conftest import codes, extraction

# Example IBANs published in the SWIFT IBAN registry / by national banks.
VALID = [
    "CH9300762011623852957",
    "CH93 0076 2011 6238 5295 7",
    "ch93 0076 2011 6238 5295 7",
    "LI21088100002324013AA",
    "DE89370400440532013000",
    "DE89 3704 0044 0532 0130 00",
    "FR1420041010050500013M02606",
    "GB82WEST12345698765432",
    "IE29AIBK93115212345678",
    "IT60X0542811101000000123456",
    "NL91ABNA0417164300",
    "NO9386011117947",
    "IBAN CH93 0076 2011 6238 5295 7",
]

INVALID = [
    "CH9300762011623852958",   # last digit changed
    "CH9300762011623852975",   # two digits swapped
    "CH9400762011623852957",   # wrong check digits
    "DE89370400440532013001",
    "GB82WEST12345698765433",
    "CH93007620116238",        # too short for any country
    "9300762011623852957CH",   # country code at the end
    "CHAB00762011623852957",   # letters as check digits
    "CH93-0076-2011-6238-5295-?",
]


@pytest.mark.parametrize("iban", VALID)
def test_valid_ibans(iban):
    assert is_valid_iban(iban), iban_problem(iban)


@pytest.mark.parametrize("iban", INVALID)
def test_invalid_ibans(iban):
    assert not is_valid_iban(iban)


def test_mod97_is_one_for_a_valid_iban():
    assert mod97("CH9300762011623852957") == 1


def test_normalize_and_format():
    assert normalize_iban(" ch93 0076-2011.6238 5295 7 ") == "CH9300762011623852957"
    assert format_iban("CH9300762011623852957") == "CH93 0076 2011 6238 5295 7"


def test_rule_flags_invalid_iban_and_explains_why():
    ex = extraction({"payment.iban": "CH93 0076 2011 6238 5295 8"})
    assert codes(ex) == ["IBAN_INVALID"]


def test_missing_iban_is_a_missing_field_not_an_invalid_iban():
    ex = extraction({"payment.iban": None})
    assert codes(ex) == ["MISSING_FIELD"]
