"""Pure normalizers: ids, labels, motifs, XML, amounts, beneficiaries (§ 12.1)."""

import pytest

from agent_edc.edc.normalizers import (
    aggregate_amounts,
    event_label,
    get_xml_attribute,
    normalize_beneficiaries,
    normalize_edc_event,
    normalize_edc_id,
    parse_info_spec,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("01234567", "01234567"), ("1234567", "01234567"), (1234567, "01234567"), ("0123-45 67", "01234567")],
)
def test_normalize_edc_id(raw, expected):
    assert normalize_edc_id(raw) == expected


@pytest.mark.parametrize("raw", ["123ABC", "123", "123456789", ""])
def test_normalize_edc_id_rejects(raw):
    with pytest.raises(ValueError):
        normalize_edc_id(raw)


def test_composite_subtype_key():
    assert event_label("1", "1", "1") == ("Courrier (entrant ou sortant) — Arrivée courrier", "1.1")


def test_alert_subtype_uses_alert_table():
    label, code = event_label("5", "1", None)
    assert label.startswith("Alerte — ")
    assert "inconnu" not in label
    assert code == "1"


def test_unknown_subtype():
    label, _ = event_label("6", "99999", None)
    assert label.endswith("Sous-événement inconnu")


def _event_row(**overrides):
    row = {"REFEVT": 7, "REFINTERCALAIRE": 88412, "TYPEEVT": "6", "SOUSTYPEEVT": "16", "BLOCNOTEEVT": None}
    return row | overrides


def test_motif_fin_traitement_by_default():
    event = normalize_edc_event(
        _event_row(INFOSPECEVT='<vo nom="racine"><string nom="motif">1</string></vo>')
    )
    assert event.motif and event.motif != "1"


def test_motif_attente_on_m8():
    xml = '<vo nom="racine"><string nom="motif">1</string></vo>'
    fin = normalize_edc_event(_event_row(INFOSPECEVT=xml)).motif
    attente = normalize_edc_event(_event_row(SOUSTYPEEVT="M8", INFOSPECEVT=xml)).motif
    assert attente != fin


def test_unknown_motif_kept_raw():
    xml = '<vo nom="racine"><string nom="motif">ZZ</string></vo>'
    assert normalize_edc_event(_event_row(INFOSPECEVT=xml)).motif == "ZZ"


def test_event_typed_fields():
    event = normalize_edc_event(_event_row())
    assert event.event_id == "7"
    assert event.intercalaire_ref == "88412"
    assert event.type_code == "6"
    assert event.subtype_code == "16"


def test_xml_recursive_and_unreadable():
    element = parse_info_spec('<vo><vo><string nom="deep">ok</string></vo></vo>')
    assert get_xml_attribute(element, "deep") == "ok"
    assert parse_info_spec("<not xml") is None
    assert get_xml_attribute(None, "deep") is None


def test_aggregate_amounts_centimes_to_euros():
    rows = [
        {"REFINTERCALAIRE": 1, "MONTANTPAYE": "150000"},
        {"REFINTERCALAIRE": 1, "MONTANTPAYE": "50"},
        {"REFINTERCALAIRE": 2, "MONTANTPAYE": None},
        {"REFINTERCALAIRE": 2, "MONTANTPAYE": ""},
    ]
    assert aggregate_amounts(rows, "MONTANTPAYE") == {"1": 1500.5}


def test_normalize_beneficiaries():
    rows = [
        {"REFINTERCALAIRE": 1, "TYPEINTERCALAIRE": "2", "NOMPATRONYMIQUE": "MARTIN", "PRENOM": "Claire"},
        {"REFINTERCALAIRE": 1, "TYPEINTERCALAIRE": "2", "NOMPATRONYMIQUE": "MARTIN", "PRENOM": "Claire"},
        {"REFINTERCALAIRE": 2, "TYPEINTERCALAIRE": "6"},
    ]
    result = normalize_beneficiaries(rows, {"1": 100.0}, {"1": 0.0})
    assert [b.beneficiary_id for b in result] == ["1", "2"]
    assert result[0].full_name == "MARTIN Claire"
    assert result[0].amount_remaining == 0.0
    assert result[1].presumed and result[1].full_name is None and result[1].amount_paid is None
