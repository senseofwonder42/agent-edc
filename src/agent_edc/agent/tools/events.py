"""Timeline tools: list, search, detail, statistics. Memory only, zero SQL."""

from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent_edc.agent.state import AgentState
from agent_edc.agent.tools._common import NO_SNAPSHOT, agent_settings, get_snapshot, now, with_freshness
from agent_edc.formatting import (
    TRUNCATION_HINT,
    format_event_full,
    format_event_lines,
    format_int,
    format_search_hits,
    format_timeline_stats,
)
from agent_edc.observability import record_tool_metadata, traced_tool
from agent_edc.snapshot import (
    clamp_page,
    filter_events,
    find_event,
    paginate,
    parse_french_date,
    timeline_stats,
)
from agent_edc.snapshot import search_events as search_snapshot


def _describe_filters(
    date_min: str | None, date_max: str | None, type_evenement: str | None, beneficiaire_id: str | None
) -> str:
    filters = []
    if type_evenement:
        filters.append(f"type « {type_evenement} »")
    if date_min:
        filters.append(f"à partir du {date_min}")
    if date_max:
        filters.append(f"jusqu'au {date_max}")
    if beneficiaire_id:
        filters.append(f"bénéficiaire {beneficiaire_id}")
    return ", ".join(filters)


@tool("lister_evenements", parse_docstring=True)
@traced_tool
def list_events(
    state: Annotated[AgentState, InjectedState],
    decalage: int = 0,
    limite: int | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
    type_evenement: str | None = None,
    beneficiaire_id: str | None = None,
    ordre: Literal["recent", "ancien"] = "recent",
) -> str:
    """Liste les événements du dossier, avec filtres et pagination.

    Les détails sont tronqués à une ligne : pour citer un événement, appelle
    « detail_evenement » avec sa référence.

    Args:
        decalage: Nombre d'événements à sauter (0 pour la première page).
        limite: Nombre d'événements à renvoyer (20 par défaut, 50 au maximum).
        date_min: Ne garder que les événements à partir de cette date, au
            format JJ/MM/AAAA.
        date_max: Ne garder que les événements jusqu'à cette date, au format
            JJ/MM/AAAA.
        type_evenement: Filtre sur le type, par son code ('1' courrier,
            '3' communication, '5' alerte, '6' événement standard) ou par un
            morceau de son libellé, par exemple « alerte ».
        beneficiaire_id: Ne garder que les événements rattachés à ce
            bénéficiaire (référence donnée par « lister_beneficiaires »).
        ordre: « recent » du plus récent au plus ancien (défaut), « ancien »
            dans l'ordre chronologique.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT

    bounds = {}
    for name, value in (("date_min", date_min), ("date_max", date_max)):
        if value:
            try:
                bounds[name] = parse_french_date(value)
            except ValueError:
                # Never ignore a filter silently: that would produce a wrong answer.
                return f"Date illisible : « {value} ». Utilise le format JJ/MM/AAAA."

    settings = agent_settings()
    requested = settings.page_size if limite is None else limite
    offset, limit = clamp_page(decalage, requested, settings.max_page_size)
    events = filter_events(
        snapshot.events,
        date_min=bounds.get("date_min"),
        date_max=bounds.get("date_max"),
        type_filter=type_evenement,
        beneficiary_id=beneficiaire_id,
        order=ordre,
    )
    filters = _describe_filters(date_min, date_max, type_evenement, beneficiaire_id)
    total = len(snapshot.events)
    if not events:
        text = f"Aucun événement ne correspond ({format_int(total)} événements au total)."
        if filters:
            text += f" Filtres appliqués : {filters}."
        return with_freshness(state, text)
    if offset >= len(events):
        return (
            f"{format_int(len(events))} événements correspondent ; le décalage {offset} est hors bornes."
            if filters
            else f"Le dossier compte {format_int(total)} événements ; le décalage {offset} est hors bornes."
        )

    page = paginate(events, offset, limit)
    lines, truncated = format_event_lines(page.items)
    record_tool_metadata(truncated=truncated)
    head = f"Événements {offset + 1} à {offset + len(page.items)} sur {format_int(page.total)}"
    head += f" (filtre : {filters})." if filters else "."
    if requested > settings.max_page_size:
        head += f" Limite ramenée à {settings.max_page_size}."
    out = [head, *lines]
    if truncated:
        out.append(TRUNCATION_HINT)
    if page.next_offset is not None:
        out.append(f"Page suivante : decalage={page.next_offset}.")
    return with_freshness(state, "\n".join(out))


@tool("chercher_evenements", parse_docstring=True)
@traced_tool
def search_events(
    requete: str,
    state: Annotated[AgentState, InjectedState],
    limite: int | None = None,
) -> str:
    """Cherche un mot ou une expression dans tout le contenu des événements.

    La recherche porte sur le libellé, le détail (bloc-note de l'événement),
    le commentaire, le motif et la nature de l'interlocuteur. Elle ignore la
    casse et les accents. C'est l'outil à privilégier pour retrouver une trace
    précise : « notaire », « relance », « succession vacante », un nom propre.

    Args:
        requete: Le mot ou l'expression à chercher.
        limite: Nombre de résultats à renvoyer (20 par défaut, 50 au maximum).
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    if len(requete.strip()) < 2:
        return "Précise un terme d'au moins deux caractères."
    settings = agent_settings()
    _, limit = clamp_page(0, settings.page_size if limite is None else limite, settings.max_page_size)
    hits = search_snapshot(snapshot.events, requete)
    record_tool_metadata(match_count=len(hits), truncated=True)
    return with_freshness(state, format_search_hits(hits, requete.strip(), limit))


@tool("detail_evenement", parse_docstring=True)
@traced_tool
def get_event(
    evenement_id: str,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Donne le contenu complet d'un événement, sans aucune troncature.

    À appeler dès qu'un événement repéré dans une liste ou une recherche doit
    être cité ou compris précisément : le détail y figure en entier, y compris
    les bloc-notes longs.

    Args:
        evenement_id: Référence de l'événement, telle qu'affichée dans les
            listes.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    event = find_event(snapshot, evenement_id)
    if event is None:
        return (
            f"Aucun événement {evenement_id} dans ce dossier. "
            "Utilise « lister_evenements » pour voir les références disponibles."
        )
    beneficiary = next(
        (b for b in snapshot.beneficiaries if b.beneficiary_id == event.intercalaire_ref), None
    )
    return with_freshness(state, format_event_full(event, beneficiary))


@tool("statistiques_chronologie", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def get_timeline_stats(state: Annotated[AgentState, InjectedState]) -> str:
    """Donne la forme de la chronologie : volume, période couverte,
    répartition par type, et surtout les périodes sans aucun événement.

    C'est l'outil le plus direct pour repérer un dossier laissé en sommeil :
    un intervalle de plusieurs mois sans le moindre acte est le premier signe
    d'une gestion défaillante. Ne remplace pas la lecture des événements, mais
    indique où regarder.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    return with_freshness(state, format_timeline_stats(timeline_stats(snapshot.events), now()))
