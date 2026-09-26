"""Domain models of an E-décès case file: dossier, events, beneficiaries.

No I/O and no rendering here: loading lives in ``edc/loader.py`` and the
French text shown to the model in ``formatting.py``.

PORTED FROM: files_late_fee/src/deces_risk/models/core.py
PORTED ON:   2026-09-18
DIVERGENCE:  ``raw`` removed everywhere (§ 2.3 b); typed fields added
             (etat_precision, effect_date, intercalaire_ref, update_date,
             type_code, subtype_code, comment, birth_date); SourceSystem,
             Affair, Document, merge_events and to_prompt not ported.

Every field carries a default so that a snapshot persisted by an older
version of these models can always be read back (§ 11.2).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EdcDossier(BaseModel):
    """The E-décès dossier record."""

    edc_id: str
    ref_dossier: str | None = None
    etat: str | None = None
    etat_precision: str | None = None
    network_code: str | None = None
    network_label: str | None = None
    creation_date: datetime | None = None
    effect_date: datetime | None = None
    global_comment: str | None = None


class Event(BaseModel):
    """A normalized EDC event.

    ``details`` is the manager's bloc-note, reassembled in full (§ 7);
    ``comment`` is the distinct ``commentaire`` node of the event XML and is
    never concatenated to ``details``.
    """

    event_id: str
    intercalaire_ref: str | None = None
    date: datetime | None = None
    update_date: datetime | None = None
    label: str = "événement"
    type_code: str | None = None
    subtype_code: str | None = None
    details: str | None = None
    comment: str | None = None
    motif: str | None = None
    interlocuteur: str | None = None


class Beneficiary(BaseModel):
    """A beneficiary known to EDC, with its payment amounts.

    Amounts are in euros (EDC stores centimes; the normalizer converts).
    ``amount_remaining == 0`` means fully paid; ``None`` means no amount was
    ever ordered — two different situations (§ 6.5).
    """

    beneficiary_id: str
    full_name: str | None = None
    birth_date: datetime | None = None
    presumed: bool = False
    amount_paid: float | None = None
    amount_remaining: float | None = None


class CaseFile(BaseModel):
    """The in-memory snapshot of one dossier; events are sorted chronologically."""

    edc_id: str
    dossier: EdcDossier | None = None
    events: list[Event] = Field(default_factory=list)
    beneficiaries: list[Beneficiary] = Field(default_factory=list)
