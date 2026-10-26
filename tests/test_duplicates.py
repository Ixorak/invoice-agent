"""DUPLICATE (another file, same supplier and number) vs already processed (same file)."""

from invoice_agent.registry import Registry, same_supplier
from invoice_agent.textutil import normalize_invoice_number, normalize_supplier_name, normalize_vat_id

from .conftest import codes, extraction


def _registry(tmp_path, **record):
    reg = Registry(tmp_path / "registry.json")
    reg.add(sha256=record.get("sha256", "a" * 64), file="first.pdf", processed_at="2026-03-02T10:00:00+01:00",
            status="OK", supplier_name=record.get("name", "Muster Handel AG"),
            vat_id=record.get("vat_id", "CHE-123.456.789 MWST"),
            invoice_number=record.get("number", "R-1001"), json_report=None)
    return reg


def test_same_vat_id_and_number_is_a_duplicate(tmp_path):
    reg = _registry(tmp_path)
    found = reg.find_duplicate("Muster Handel AG", "CHE-123.456.789 MWST", "R-1001")
    assert found is not None
    assert codes(extraction(), duplicate_of=found.as_previous()) == ["DUPLICATE"]


def test_vat_id_and_number_formatting_does_not_matter(tmp_path):
    reg = _registry(tmp_path)
    assert reg.find_duplicate("Another spelling Ltd", "CHE123456789", "R 1001") is not None
    assert reg.find_duplicate(None, "che-123 456 789 tva", "r-1001") is not None


def test_other_number_or_other_supplier_is_not_a_duplicate(tmp_path):
    reg = _registry(tmp_path)
    assert reg.find_duplicate("Muster Handel AG", "CHE-123.456.789 MWST", "R-1002") is None
    assert reg.find_duplicate("Muster Handel AG", "CHE-987.654.321 MWST", "R-1001") is None


def test_without_vat_id_the_normalized_name_is_used(tmp_path):
    reg = _registry(tmp_path, vat_id=None, name="Papeterie Exemple Sàrl")
    assert reg.find_duplicate("PAPETERIE EXEMPLE SARL", None, "R-1001") is not None
    assert reg.find_duplicate("Papeterie Exemple", "CHE-111.222.333 TVA", "R-1001") is not None
    assert reg.find_duplicate("Papeterie Autre Sàrl", None, "R-1001") is None


def test_duplicates_are_found_across_runs(tmp_path):
    reg = _registry(tmp_path)
    reg.save()
    reloaded = Registry.load(tmp_path / "registry.json")
    assert reloaded.find_duplicate("Muster Handel AG", "CHE-123.456.789 MWST", "R-1001") is not None


def test_already_processed_file_is_found_by_its_sha256(tmp_path):
    reg = _registry(tmp_path, sha256="b" * 64)
    reg.save()
    reloaded = Registry.load(tmp_path / "registry.json")
    assert reloaded.find_by_hash("b" * 64) is not None
    assert reloaded.find_by_hash("c" * 64) is None


def test_no_invoice_number_means_no_duplicate_check(tmp_path):
    reg = _registry(tmp_path)
    assert reg.find_duplicate("Muster Handel AG", "CHE-123.456.789 MWST", None) is None


def test_normalization_helpers():
    assert normalize_vat_id("CHE-482.915.306 TVA") == "CHE482915306"
    assert normalize_vat_id("UID: CHE-482.915.306 MWST") == "CHE482915306"
    assert normalize_vat_id("IE 9999999XA (fictional)") == "IE9999999XA"
    assert normalize_vat_id("VAT reg.: DE 123 456 789") == "DE123456789"
    assert normalize_supplier_name("Primeurs du Lac SA") == "primeurs du lac"
    assert normalize_supplier_name("Nordwerk GmbH") == "nordwerk"
    assert normalize_supplier_name("S.A.R.L.") == "sarl"  # a name is never emptied
    assert normalize_invoice_number("F-2026-0418") == "F20260418"
    assert same_supplier("CHE1", "a", "CHE1", "b")
    assert not same_supplier("CHE1", "a", "CHE2", "a")
    assert same_supplier(None, "a", "CHE2", "a")
