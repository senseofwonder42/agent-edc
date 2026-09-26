"""SQL queries for the E-décès (ESD) database.

Bind variables only (``:id_dossier``) — never f-strings. Amounts extracted
from the intercalaire XML are in **centimes** (the normalizers convert).

PORTED FROM: files_late_fee/src/deces_risk/datasources/edc/queries.py
PORTED ON:   2026-09-18
DIVERGENCE:  none (verbatim). Must stay identical to the source.
"""

from __future__ import annotations

#: Dossier record + global note. The dossier-level bloc-note (VARCHAR2 300,
#: often truncated at 256 chars) is concatenated with the ``blocNoteExtensible``
#: field of the ``INFOSPECDOSSIER`` XML CLOB, which holds the overflow.
QUERY_DOSSIER = """
SELECT
    d.IDDOSSIER,
    d.REFDOSSIER,
    d.ETATDOSSIER,
    d.ETATPRECISIONDOSSIER,
    d.CRITEREDOSSIER1,
    d.DATECREATIONDOSSIER,
    d.DATEEFFETDOSSIER,
    TO_CLOB(d.BLOCNOTEDOSSIER)
    || CASE
           WHEN d.INFOSPECDOSSIER IS NOT NULL
           THEN NVL(
               XMLCAST(
                   XMLQuery(
                       '/vo[@nom="racine"]/string[@nom="blocNoteExtensible"]'
                       PASSING XMLTYPE(d.INFOSPECDOSSIER)
                       RETURNING CONTENT
                   ) AS CLOB
               ), TO_CLOB(''))
           ELSE TO_CLOB('')
       END AS BLOC_NOTE
FROM ESDDOSSIER d
WHERE d.IDDOSSIER = :id_dossier
"""

#: Event history with the business comments and the XML payloads
#: (``INFOSPECEVT`` carries the motif/commentaire, ``INFOSPECINTERCALAIRE``
#: the interlocutor nature). One row per event × intercalaire.
QUERY_EVENTS = """
SELECT
    d.REFDOSSIER,
    d.IDDOSSIER,
    i.REFINTERCALAIRE,
    i.TYPEINTERCALAIRE,
    i.INFOSPECINTERCALAIRE,
    e.REFEVT,
    e.TYPEEVT,
    e.SOUSTYPEEVT,
    e.CRITEREEVT2,
    e.BLOCNOTEEVT,
    e.INFOSPECEVT,
    e.DATECREATIONEVT,
    e.DATEMAJEVT
FROM ESDDOSSIER d
INNER JOIN ESDINTERCALAIRE i ON d.REFDOSSIER = i.REFDOSSIER
INNER JOIN ESDEVENEMENT e ON i.REFINTERCALAIRE = e.REFINTERCALAIRE
WHERE d.IDDOSSIER = :id_dossier
ORDER BY e.DATECREATIONEVT
"""

#: Known beneficiaries: intercalaires of type '2' (Bénéficiaire) and
#: '6' (Bénéficiaire présumé), with the person identity when linked
#: (chain ESDROLE → ESDPERSONNE → ESDPERSONNEPHYSIQUE, patterned after
#: ``query_info_defunt``).
QUERY_BENEFICIARIES = """
SELECT
    d.IDDOSSIER,
    i.REFINTERCALAIRE,
    i.TYPEINTERCALAIRE,
    pp.NOMPATRONYMIQUE,
    pp.NOMMARITAL,
    pp.PRENOM,
    pp.DATENAISSANCE
FROM ESDDOSSIER d
INNER JOIN ESDINTERCALAIRE i ON d.REFDOSSIER = i.REFDOSSIER
LEFT JOIN ESDROLE r ON i.REFINTERCALAIRE = r.REFINTERCALAIRE
LEFT JOIN ESDPERSONNE p ON r.REFPERSONNE = p.REFPERSONNE
LEFT JOIN ESDPERSONNEPHYSIQUE pp ON p.REFPERSONNE = pp.REFPERSONNE
WHERE d.IDDOSSIER = :id_dossier
  AND i.TYPEINTERCALAIRE IN ('2', '6')
"""

#: Amounts paid to the beneficiaries: one row per ``listeOrdonnancements``
#: item of each beneficiary intercalaire (values in centimes, as strings).
QUERY_AMOUNTS_PAID = """
SELECT
    d.IDDOSSIER,
    i.REFINTERCALAIRE,
    x.MONTANTPAYE
FROM ESDDOSSIER d
INNER JOIN ESDINTERCALAIRE i ON d.REFDOSSIER = i.REFDOSSIER
CROSS JOIN XMLTABLE(
    '/vo/collection[@nom="listeOrdonnancements"]/item/vo'
    PASSING XMLTYPE(i.INFOSPECINTERCALAIRE)
    COLUMNS
        MONTANTPAYE VARCHAR2(15) PATH 'string[@nom="montantPaye"]'
) x
WHERE d.IDDOSSIER = :id_dossier
  AND i.TYPEINTERCALAIRE IN ('2', '6')
"""

#: Amounts remaining to pay, same structure as :data:`QUERY_AMOUNTS_PAID`.
QUERY_AMOUNTS_REMAINING = """
SELECT
    d.IDDOSSIER,
    i.REFINTERCALAIRE,
    x.MONTANTRESTANTAPAYER
FROM ESDDOSSIER d
INNER JOIN ESDINTERCALAIRE i ON d.REFDOSSIER = i.REFDOSSIER
CROSS JOIN XMLTABLE(
    '/vo/collection[@nom="listeOrdonnancements"]/item/vo'
    PASSING XMLTYPE(i.INFOSPECINTERCALAIRE)
    COLUMNS
        MONTANTRESTANTAPAYER VARCHAR2(15) PATH 'string[@nom="montantRestantAPayer"]'
) x
WHERE d.IDDOSSIER = :id_dossier
  AND i.TYPEINTERCALAIRE IN ('2', '6')
"""
