"""Nomenclature lookup tool. Reads constant tables only — callable before any load."""

from typing import Literal

from langchain_core.tools import tool

from agent_edc.edc.nomenclatures import NOMENCLATURES
from agent_edc.observability import traced_tool

#: French name of each table, as used in the answers.
TABLE_LABELS = {
    "type_evenement": "des types d'événement",
    "sous_type_evenement": "des sous-types d'événement",
    "sous_type_alerte": "des sous-types d'alerte",
    "type_intercalaire": "des types d'intercalaire",
    "motif_fin_traitement": "des motifs de fin de traitement",
    "motif_attente": "des motifs d'attente",
    "interlocuteur": "des natures d'interlocuteur",
}

#: Up to this many codes, an unknown-code answer lists every valid code.
_LIST_CODES_UP_TO = 30


@tool("libelle_nomenclature", parse_docstring=True)
@traced_tool
def lookup_nomenclature(
    table: Literal[
        "type_evenement",
        "sous_type_evenement",
        "sous_type_alerte",
        "type_intercalaire",
        "motif_fin_traitement",
        "motif_attente",
        "interlocuteur",
    ],
    code: str,
) -> str:
    """Traduit un code E-décès en son libellé officiel.

    À utiliser dès qu'un code brut doit être expliqué au gestionnaire. Ne
    JAMAIS deviner la signification d'un code : si cet outil ne le connaît
    pas, il faut le dire.

    Args:
        table: La nomenclature à consulter.
        code: Le code à traduire, par exemple « 5 », « 1.1 » ou « M8 ». « * »
            renvoie la table entière.
    """
    entries = NOMENCLATURES.get(table)
    if entries is None:
        return f"Nomenclature inconnue : « {table} ». Tables disponibles : {', '.join(NOMENCLATURES)}."
    label = TABLE_LABELS[table]
    wanted = code.strip()
    if wanted == "*":
        lines = [f"Nomenclature {label} ({len(entries)} codes) :"]
        lines += [f"  {key} → {value}" for key, value in entries.items()]
        return "\n".join(lines)
    if wanted in entries:
        return f"Nomenclature {label} · code « {wanted} » → « {entries[wanted]} »"
    if len(entries) <= _LIST_CODES_UP_TO:
        known = f"{len(entries)} codes connus : {', '.join(entries)}"
    else:
        known = f'{len(entries)} codes connus ; code="*" pour la table entière'
    return f"Le code « {wanted} » n'existe pas dans la nomenclature {label} ({known})."
