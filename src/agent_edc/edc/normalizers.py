"""Raw EDC rows → domain models. Pure functions, testable without Oracle.

PORTED FROM: files_late_fee/src/deces_risk/datasources/edc/source.py
             (+ parse_datetime from datasources/common.py)
PORTED ON:   2026-09-18
DIVERGENCE:  intentional —
             * the event bloc-note is reassembled from ``blocNoteExtensible``
               with NO separator (§ 7), newlines are preserved (§ 7.5);
             * ``details`` (bloc-note) and ``comment`` (XML ``commentaire``)
               are kept separate instead of ``". ".join(...)`` (§ 7.4);
             * ``raw`` removed, typed fields added (§ 2.3 b);
             * ``normalize_dossier`` extracted from ``EdcDataSource``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from loguru import logger

from agent_edc.edc.nomenclatures import (
    INTERLOCUTEUR,
    MOTIF_ATTENTE,
    MOTIF_FT,
    SOUS_TYPE_ALERTE,
    SOUS_TYPE_EVT,
    TYPE_EVT,
)
from agent_edc.models import Beneficiary, EdcDossier, Event

#: ESDDOSSIER.CRITEREDOSSIER1 → network label.
RESEAU_EDC = {"0": "Trésor", "4": "Poste", "8": "CE"}

#: Event subtypes whose XML ``motif`` uses the "mise en attente" nomenclature.
_SOUS_TYPES_ATTENTE = ("M8",)

#: BLOCNOTEEVT value written by EDC on automatic events; noise when alone.
AUTOMATIC_EVENT_SENTINEL = "Evénement Automatique"


def normalize_edc_id(value: str | int) -> str:
    """Normalize an E-décès dossier id to its 8-digit form.

    Args:
        value: Raw dossier id (int or string, possibly with dashes/spaces,
            possibly 7 digits with the leading zero dropped).

    Returns:
        The 8-digit dossier id.

    Raises:
        ValueError: If the cleaned id is not 7 or 8 digits.
    """
    edc_id = str(value).replace("-", "").replace(" ", "").strip()
    if len(edc_id) == 7:
        edc_id = "0" + edc_id
    if len(edc_id) != 8 or not edc_id.isdigit():
        raise ValueError(f"Identifiant de dossier E-décès invalide : {value!r} (8 chiffres attendus)")
    return edc_id


def parse_datetime(value: Any) -> datetime | None:
    """Coerce a raw payload value into a ``datetime``.

    Args:
        value: ``None``, a ``datetime``, epoch milliseconds or an ISO-8601 string.

    Returns:
        The parsed datetime, or ``None`` when the value cannot be parsed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Unparseable datetime value discarded")
            return None
    logger.debug("Unsupported datetime type discarded: {}", type(value).__name__)
    return None


def parse_info_spec(value: Any) -> ET.Element | None:
    """Parse an ``INFOSPEC*`` XML payload (str or Oracle LOB) into an element.

    The ``read()`` branch is a zero-cost safety net for a cursor without the
    LOB output handler (§ 7.7).
    """
    if value is None:
        return None
    text = value.read() if hasattr(value, "read") else value
    if not text:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        logger.debug("Unparseable INFOSPEC XML discarded: {}", exc)
        return None


def get_xml_attribute(element: ET.Element | None, ref_attrib: str) -> str | None:
    """Recursively find the text of the XML node whose ``nom`` attribute matches.

    Args:
        element: Parsed ``INFOSPEC*`` element (or ``None``).
        ref_attrib: Value of the ``nom`` attribute to look for.

    Returns:
        The node text, or ``None`` when absent.
    """
    if element is None:
        return None
    if element.attrib.get("nom") == ref_attrib:
        return element.text
    for child in element:
        result = get_xml_attribute(child, ref_attrib)
        if result:
            return result
    return None


def event_label(type_evt: str | None, sous_type: str | None, critere: str | None) -> tuple[str, str | None]:
    """French label of an EDC event and the nomenclature key of its subtype.

    For subtypes '1' and '3' a composite key ``"<soustype>.<critere>"`` is
    used when CRITEREEVT2 is '1' or '2'; alerts (TYPEEVT '5') use the
    dedicated alert nomenclature.

    Returns:
        ``(label, subtype_code)`` where ``subtype_code`` is the key actually
        looked up (e.g. ``"1.1"``), or ``None`` when there is no subtype.
    """
    type_label = TYPE_EVT.get(str(type_evt), "événement inconnu").capitalize()
    sous_type = str(sous_type) if sous_type is not None else ""
    if str(type_evt) == "5":
        key = sous_type
        sous_label = SOUS_TYPE_ALERTE.get(key, "sous-événement inconnu").capitalize()
    else:
        key = sous_type
        if sous_type in ("1", "3") and str(critere) in ("1", "2"):
            key = f"{sous_type}.{critere}"
        sous_label = SOUS_TYPE_EVT.get(key, "sous-événement inconnu").capitalize()
    return f"{type_label} — {sous_label}", key or None


def stitch_bloc_note(base: str | None, overflow: str | None) -> str | None:
    """Reassemble a bloc-note truncated at the VARCHAR2 boundary.

    EDC stores the beginning of a long note in a VARCHAR2 column and the
    remainder in the ``blocNoteExtensible`` node of the associated XML CLOB.
    The cut can fall mid-word, so the two parts are concatenated with NO
    separator: anything inserted here would corrupt the word straddling the
    boundary. The presence of an overflow is the only criterion — never the
    length of ``base`` (§ 7.1).

    Args:
        base: Value of the VARCHAR2 bloc-note column (possibly truncated).
        overflow: Value of the ``blocNoteExtensible`` XML node.

    Returns:
        The reassembled note, or None when both parts are empty.
    """
    parts = [part for part in (base, overflow) if part and part.strip()]
    return "".join(parts) or None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def normalize_edc_event(row: dict[str, Any]) -> Event:
    """Convert one ``QUERY_EVENTS`` row into a normalized :class:`Event`.

    The event-level bloc-note is stitched here, in Python, whereas the
    dossier-level one is stitched in SQL (``QUERY_DOSSIER``). The asymmetry is
    deliberate: stitching happens where the XML is already available at no
    extra cost — ``INFOSPECEVT`` is fetched and parsed anyway (§ 7.3).

    Args:
        row: Raw row (event + intercalaire columns, XML payloads as str/LOB).

    Returns:
        The normalized event.
    """
    info_evt = parse_info_spec(row.get("INFOSPECEVT"))
    info_inter = parse_info_spec(row.get("INFOSPECINTERCALAIRE"))

    motif = None
    motif_id = get_xml_attribute(info_evt, "motif")
    if motif_id:
        table = MOTIF_ATTENTE if str(row.get("SOUSTYPEEVT")) in _SOUS_TYPES_ATTENTE else MOTIF_FT
        motif = table.get(motif_id, motif_id)

    interlocuteur = None
    interlocuteur_id = get_xml_attribute(info_inter, "natureInterlocuteur")
    if interlocuteur_id:
        interlocuteur = INTERLOCUTEUR.get(interlocuteur_id, interlocuteur_id)

    base_note = row.get("BLOCNOTEEVT")
    overflow = get_xml_attribute(info_evt, "blocNoteExtensible")
    # The "automatic event" sentinel is only noise when it stands alone.
    if base_note == AUTOMATIC_EVENT_SENTINEL and not overflow:
        base_note = None
    details = stitch_bloc_note(_optional_str(base_note), overflow)

    comment = get_xml_attribute(info_evt, "commentaire")
    label, subtype_code = event_label(row.get("TYPEEVT"), row.get("SOUSTYPEEVT"), row.get("CRITEREEVT2"))

    return Event(
        event_id=str(row.get("REFEVT") or ""),
        intercalaire_ref=_optional_str(row.get("REFINTERCALAIRE")),
        date=parse_datetime(row.get("DATECREATIONEVT")),
        update_date=parse_datetime(row.get("DATEMAJEVT")),
        label=label,
        type_code=_optional_str(row.get("TYPEEVT")),
        subtype_code=subtype_code,
        details=details,
        comment=comment if comment and comment.strip() else None,
        motif=motif,
        interlocuteur=interlocuteur,
    )


def normalize_dossier(edc_id: str, row: dict[str, Any]) -> EdcDossier:
    """Convert the ``QUERY_DOSSIER`` row into an :class:`EdcDossier`.

    ``BLOC_NOTE`` already holds the SQL-side concatenation of the VARCHAR2
    column and its ``blocNoteExtensible`` overflow; it is kept as is.

    Args:
        edc_id: Normalized 8-digit dossier id.
        row: The single row returned by ``QUERY_DOSSIER``.

    Returns:
        The dossier record.
    """
    network_code = _optional_str(row.get("CRITEREDOSSIER1"))
    note = row.get("BLOC_NOTE")
    note = str(note) if note is not None else None
    return EdcDossier(
        edc_id=edc_id,
        ref_dossier=_optional_str(row.get("REFDOSSIER")),
        etat=_optional_str(row.get("ETATDOSSIER")),
        etat_precision=_optional_str(row.get("ETATPRECISIONDOSSIER")),
        network_code=network_code,
        network_label=RESEAU_EDC.get(str(network_code), network_code),
        creation_date=parse_datetime(row.get("DATECREATIONDOSSIER")),
        effect_date=parse_datetime(row.get("DATEEFFETDOSSIER")),
        global_comment=note if note and note.strip() else None,
    )


def aggregate_amounts(rows: list[dict[str, Any]], column: str) -> dict[str, float]:
    """Sum the ordonnancement amounts per beneficiary, converting centimes → euros.

    Args:
        rows: Rows from the amount queries (one per ordonnancement).
        column: Amount column name (``MONTANTPAYE`` or ``MONTANTRESTANTAPAYER``).

    Returns:
        ``REFINTERCALAIRE`` → total amount in euros.
    """
    totals: dict[str, float] = {}
    for row in rows:
        value = row.get(column)
        if value in (None, ""):
            continue
        ref = str(row.get("REFINTERCALAIRE"))
        totals[ref] = totals.get(ref, 0.0) + int(value) / 100
    return totals


def normalize_beneficiaries(
    rows: list[dict[str, Any]],
    paid: dict[str, float],
    remaining: dict[str, float],
) -> list[Beneficiary]:
    """Build the beneficiaries from the identity rows and the amount totals.

    Args:
        rows: Rows from ``QUERY_BENEFICIARIES``.
        paid: Amounts paid per ``REFINTERCALAIRE`` (euros).
        remaining: Amounts remaining per ``REFINTERCALAIRE`` (euros).

    Returns:
        One beneficiary per distinct intercalaire.
    """
    beneficiaries: dict[str, Beneficiary] = {}
    for row in rows:
        ref = str(row.get("REFINTERCALAIRE"))
        if ref in beneficiaries:
            continue
        last_name = row.get("NOMPATRONYMIQUE") or row.get("NOMMARITAL")
        first_name = row.get("PRENOM")
        full_name = " ".join(part for part in (last_name, first_name) if part) or None
        beneficiaries[ref] = Beneficiary(
            beneficiary_id=ref,
            full_name=full_name,
            birth_date=parse_datetime(row.get("DATENAISSANCE")),
            presumed=str(row.get("TYPEINTERCALAIRE")) == "6",
            amount_paid=paid.get(ref),
            amount_remaining=remaining.get(ref),
        )
    return list(beneficiaries.values())
