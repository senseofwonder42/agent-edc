# Architecture — `agent-edc`

> **Agent conversationnel sur un dossier E-décès.**
> Un gestionnaire ouvre une conversation, donne un identifiant de dossier à
> 8 chiffres, et pose ses questions en français : *« ce dossier est-il bien
> géré ? »*, *« que s'est-il passé le 14/07/2024 ? »*, *« où en est la
> bénéficiaire Martin ? »*.

Ce document est le **plan de construction** : il est écrit pour être relu et
critiqué **avant** qu'une seule ligne de Python n'existe. Il vise un data
scientist junior qui découvre à la fois EDC, LangGraph et le projet.

## Sommaire

| § | Section |
|---|---|
| [1](#1--contexte-périmètre-et-non-objectifs) | Contexte, périmètre et non-objectifs |
| [2](#2--portage-autonome-depuis-deces-risk) | Portage autonome depuis `deces-risk` |
| [3](#3--arborescence-cible) | Arborescence cible |
| [4](#4--le-graphe-et-son-état) | Le graphe et son état |
| [5](#5--stratégie-daccès-aux-données-la-décision-structurante) | Stratégie d'accès aux données (la décision structurante) |
| [6](#6--catalogue-doutils) | Catalogue d'outils |
| [7](#7--reconstruction-du-bloc-note-section-critique) | Reconstruction du bloc-note (section critique) |
| [8](#8--le-backend-llm) | Le backend LLM |
| [9](#9--prompt-système) | Prompt système |
| [10](#10--observabilité-langfuse) | Observabilité Langfuse |
| [11](#11--service-et-persistance) | Service et persistance |
| [12](#12--stratégie-de-tests) | Stratégie de tests |
| [13](#13--phase-2--la-voie-deep-agents) | Phase 2 — la voie deep-agents |
| [14](#14--risques-et-questions-ouvertes) | Risques et questions ouvertes |
| [A](#annexe-a--envexample-et-configyaml) | Annexe A — `.env.example` et `config.yaml` |
| [B](#annexe-b--glossaire-edc) | Annexe B — glossaire EDC |

---

## 1 — Contexte, périmètre et non-objectifs

### 1.1 Le besoin

Aujourd'hui, pour juger si un dossier décès a été correctement traité, un
gestionnaire (ou un auditeur) ouvre l'application E-décès et fait défiler à la
main une chronologie qui compte couramment **100 à 500 événements**, dont
l'essentiel de la valeur tient dans des **bloc-notes** saisis librement. C'est
lent, et c'est là que se perdent les délais qui deviennent des intérêts de
retard.

`agent-edc` répond à ce besoin par une conversation : l'agent charge le dossier
une fois, puis répond avec des **citations datées** tirées des données réelles.

### 1.2 Ce que l'agent est

| Propriété | Choix | Pourquoi |
|---|---|---|
| Portée | **Un seul dossier par conversation** | Le fil (*thread*) porte l'instantané d'un dossier ; c'est ce qui rend l'accès aux données quasi gratuit (§ 5). |
| Accès | **Strictement en lecture** | Aucun `INSERT`/`UPDATE`/`DELETE` n'est écrit nulle part. Un agent qui écrit dans EDC est un autre projet, avec une autre gouvernance. |
| Source | **EDC uniquement** | Numéa, Yvoire et la GED sont hors périmètre v1 (§ 1.4). |
| Nature | **Agent ReAct**, pas un pipeline | La question n'est pas connue d'avance ; c'est le modèle qui décide quels outils appeler et dans quel ordre. |

### 1.3 Ce qui distingue ce projet de `deces-risk`

`deces-risk` (dans [`files_late_fee`](../../files_late_fee/)) est un **pipeline
batch déterministe** : un graphe LangGraph à nœuds fixes, qui prend un id,
interroge EDC + Numéa + Yvoire, fusionne, appelle le LLM une fois et produit un
verdict.

`agent-edc` est l'inverse sur trois axes :

| | `deces-risk` | `agent-edc` |
|---|---|---|
| Déclenchement | batch, une passe | conversationnel, multi-tours |
| Graphe | ~14 nœuds figés | 2 nœuds + une boucle |
| Rôle du LLM | exécute une étape | **pilote** l'exécution |
| Accès données | à chaque nœud, au fil de l'eau | **un seul chargement**, puis mémoire |
| Sortie | un rapport CSV/Parquet | une réponse en langage naturel, sourcée |

Les deux projets partagent la **couche EDC** — et c'est tout. Le § 2 explique
pourquoi elle est copiée plutôt que réutilisée.

### 1.4 Non-objectifs v1 (explicitement différés)

Chacun de ces points est *volontairement* hors périmètre. Ils sont listés pour
qu'aucun relecteur ne les croie oubliés.

| Différé | Raison |
|---|---|
| **Numéa** (API REST + Elasticsearch) | Deuxième source, deuxième modèle de données, deuxième couche d'erreurs réseau. À ajouter quand la valeur sur EDC seul sera démontrée. |
| **Yvoire / IDD** (Oracle) | Les identifiants IDD ne sont pas disponibles à ce jour (cf. `files_late_fee`). |
| **GED ADO** (documents) | Spécification d'API inconnue ; EDC ne stocke aucun document. |
| **Identité du défunt et contrats rattachés** | Nécessite `query_info_defunt` + la chaîne contrats ; utile mais non requis pour juger la *gestion*. |
| **Dossiers liés** (`INFOSPECDOSSIER`) | Fait sortir du périmètre « un dossier = un fil ». |
| **Recherche multi-dossiers / triage de portefeuille** (`query_dossiers_actifs`) | Change complètement le modèle de coût : plus d'instantané possible. C'est un autre produit. |
| **Toute écriture dans EDC** | Hors gouvernance. |
| **Audit de conformité scripté** (jeu de règles) | Prévu en phase 2 (§ 13) ; en v1 l'agent *raisonne* sur les faits, il ne *note* pas le dossier. |

---

## 2 — Portage autonome depuis `deces-risk`

### 2.1 Le principe

`agent-edc` **possède sa propre couche EDC**. Aucun `import deces_risk`, aucune
dépendance déclarée vers `files_late_fee`, aucun chemin relatif vers ce dépôt à
l'exécution.

> **Pourquoi copier plutôt que dépendre ?**
> `deces-risk` n'est pas publié sur un index de paquets ; en dépendre
> signifierait un chemin absolu dans `pyproject.toml`, donc un projet
> impossible à installer ailleurs. Par ailleurs les deux projets divergent
> déjà volontairement (§ 2.4) : le correctif bloc-note événement (§ 7) est une
> divergence *souhaitée*, pas un retard de synchronisation.

Le dépôt `files_late_fee` est **matériel de référence en lecture seule**. Rien
n'y est modifié, refactoré ni exécuté par ce projet.

### 2.2 Table de portage

Chemins sources relatifs à
[`../../files_late_fee/src/deces_risk/`](../../files_late_fee/src/deces_risk/).

| Source | Cible `agent-edc` | Adaptation |
|---|---|---|
| `datasources/edc/connection.py` | [`edc/connection.py`](../src/agent_edc/edc/connection.py) | **Logique inchangée.** `edc_connection`, `_lob_output_handler` et `fetch_rows` sont repris tels quels ; seuls les imports changent (`agent_edc.config.EDCConfig`). `ensure_thick_mode` est rapatrié ici depuis `datasources/common.py` (il n'y a plus qu'une seule base). |
| `datasources/common.py` | [`edc/normalizers.py`](../src/agent_edc/edc/normalizers.py) | **Portage partiel.** Seul `parse_datetime` est repris. `first_value` est abandonné (spécifique aux payloads Numéa). |
| `datasources/edc/queries.py` | [`edc/queries.py`](../src/agent_edc/edc/queries.py) | Les 5 requêtes sont reprises **à l'identique**, y compris `QUERY_DOSSIER` avec sa concaténation `blocNoteExtensible` côté SQL (§ 7.3). Les liaisons restent des **variables de liaison** (`:id_dossier`) — jamais de f-string comme dans les scripts d'archive. |
| `datasources/edc/nomenclatures.py` | [`edc/nomenclatures.py`](../src/agent_edc/edc/nomenclatures.py) | **Copie verbatim** des 7 tables (~300 codes). Ajout d'un seul élément : le registre `NOMENCLATURES: dict[str, dict[str, str]]` qui indexe les tables par nom, nécessaire à l'outil `libelle_nomenclature` (§ 6.6). |
| `datasources/edc/source.py` | **scindé en deux** : [`edc/normalizers.py`](../src/agent_edc/edc/normalizers.py) (pur) + [`edc/loader.py`](../src/agent_edc/edc/loader.py) (E/S) | Voir § 2.3. La classe `EdcDataSource` (3 méthodes, **3 connexions**) disparaît au profit d'une fonction `load_case_file()` (**1 connexion**). `normalize_edc_event` est corrigée (§ 7). |
| `models/core.py` | [`models.py`](../src/agent_edc/models.py) | Voir § 2.3. |
| `config.py` | [`config.py`](../src/agent_edc/config.py) | On garde `EDCConfig`, `VLMConfig`, `LangfuseConfig` tels quels. On supprime `NumeaConfig`, `IDDConfig`, `GedAdoConfig`, `relevant_document_types`. On ajoute `AgentConfig` (§ A). |
| `observability.py` | [`observability.py`](../src/agent_edc/observability.py) | `setup_logging` et `build_langfuse_callbacks` repris tels quels (le second ne lève **jamais**). `instrumented_node` est abandonné — avec 2 nœuds il n'a plus d'objet ; il est remplacé par le décorateur `traced_tool` (§ 10.4). |
| `llm/client.py` | [`llm.py`](../src/agent_edc/llm.py) | Seule `build_chat_model()` est portée. `VLMClient`, `extract_information`, `_image_blocks` et toute la partie vision disparaissent : l'agent ne lit pas de documents en v1. |

**Non porté :** `datasources/numea/`, `datasources/yvoire/`, `datasources/base.py`
(le `Protocol` `CaseDataSource` n'a plus qu'une implémentation), `llm/pdf.py`,
`llm/prompts/`, `llm/schemas.py`, `reporting.py`, `runner.py`, `graph/`
(le graphe est entièrement différent).

### 2.3 Les deux adaptations qui ne sont pas de simples copies

**a) `EdcDataSource` → `load_case_file()`**

Dans `deces-risk`, chacune des trois méthodes (`fetch_dossier`, `fetch_events`,
`fetch_beneficiaries`) ouvre **sa propre** connexion Oracle. Acceptable pour un
batch nocturne ; inacceptable dans une conversation où l'utilisateur attend.
Le portage fusionne tout dans une fonction unique qui exécute les 5 requêtes
dans **un seul** `edc_connection(...)` (§ 5.2).

**b) Les modèles perdent `raw`, gagnent des champs typés**

Dans `deces-risk`, `Event.raw`, `Beneficiary.raw` et `EdcDossier.raw` conservent
la ligne Oracle brute. On les **supprime**, pour trois raisons : (i) `raw` n'est
jamais montré au modèle (budget de tokens, § 6.8) ; (ii) l'instantané est
sérialisé dans le *checkpointer* — `raw` y triplerait le volume écrit sur disque ;
(iii) `raw` transporte des identités brutes (`NOMPATRONYMIQUE`, `DATENAISSANCE`)
et remonterait dans les traces Langfuse (§ 10.5).

Ce qui était utile dans `raw` devient un champ typé :

| Modèle | Champs v1 | Δ vs `deces-risk` |
|---|---|---|
| `EdcDossier` | `edc_id`, `ref_dossier`, `etat`, `etat_precision`, `network_code`, `network_label`, `creation_date`, `effect_date`, `global_comment` | `+ etat_precision`, `+ effect_date`, `− raw` |
| `Event` | `event_id`, `intercalaire_ref`, `date`, `update_date`, `label`, `type_code`, `subtype_code`, `details`, `comment`, `motif`, `interlocuteur` | `+ intercalaire_ref`, `+ update_date`, `+ type_code`/`subtype_code` (pour expliquer un code brut), `+ comment` (§ 7.4), `− source_system`, `− affair_id`, `− raw`, `− to_prompt()` |
| `Beneficiary` | `beneficiary_id`, `full_name`, `birth_date`, `presumed`, `amount_paid`, `amount_remaining` | `+ birth_date`, `− raw` |
| `CaseFile` | `edc_id`, `dossier`, `events`, `beneficiaries` | `− affairs`, `− documents` |

> `intercalaire_ref` mérite une justification : `QUERY_EVENTS` sélectionne
> **déjà** `i.REFINTERCALAIRE`, et `QUERY_BENEFICIARIES` utilise la même clé.
> Le conserver relie donc gratuitement chaque événement à son bénéficiaire —
> exactement ce qu'il faut pour répondre à *« où en est la bénéficiaire Y ? »*
> (§ 6.5). Ne pas le porter serait perdre une information déjà payée.

`SourceSystem`, `SOURCE_LABELS`, `Affair`, `Document`, `merge_events()` et
`to_prompt()` ne sont pas portés : une seule source, donc plus rien à étiqueter
ni à fusionner ; le rendu texte est centralisé dans `formatting.py` (§ 6.8).

### 2.4 Risque de dérive et sa mitigation

**Le risque.** Cinq éléments existent désormais en double : le SQL, les
nomenclatures, le handler LOB, les normaliseurs et les modèles. Si un code EDC
est ajouté dans `files_late_fee` (nouveau `SOUSTYPEEVT`, nouvel
`INTERLOCUTEUR`), `agent-edc` l'ignorera et affichera « sous-événement inconnu ».
Symétriquement, une correction faite ici (le bloc-note événement) ne remontera
pas là-bas.

**Les mitigations.**

1. **En-tête de provenance.** Chaque fichier porté commence par un bloc
   nommant sa source et la date du portage — pas de commentaire vague :

   ```python
   """EDC nomenclatures: code → French label mappings.

   PORTED FROM: files_late_fee/src/deces_risk/datasources/edc/nomenclatures.py
   PORTED ON:   2026-09-18
   DIVERGENCE:  none (verbatim) + NOMENCLATURES registry appended.
   """
   ```

2. **Test de parité, désactivé par défaut.**
   `tests/test_nomenclature_parity.py` charge le module source **par chemin de
   fichier** (`importlib.util.spec_from_file_location`) — donc sans créer la
   moindre dépendance — et compare les 7 tables. Il est marqué
   `@pytest.mark.parity` et `skip` si le chemin n'existe pas, de sorte que la
   suite reste verte sur une machine qui n'a pas `files_late_fee`.

   ```bash
   uv run pytest -m parity   # à lancer avant chaque livraison
   ```

3. **Table des divergences assumées**, tenue dans ce document, pour qu'une
   future comparaison ne « re-corrige » pas une correction :

   | Divergence | Statut |
   |---|---|
   | Bloc-note événement reconstitué (§ 7) | **Intentionnelle.** Correctif d'une perte de données. À remonter vers `files_late_fee` dans un ticket séparé. |
   | `details` / `comment` séparés au lieu du `". ".join(...)` | **Intentionnelle** (§ 7.4). |
   | Suppression de `raw` | **Intentionnelle** (§ 2.3 b). |
   | `load_case_file()` au lieu de `EdcDataSource` | **Intentionnelle** (§ 2.3 a). |
   | Nomenclatures, SQL, handler LOB | **Doivent rester identiques** — c'est ce que le test de parité surveille. |

4. **Aucun identifiant copié.** Les scripts d'archive et les scripts racine de
   `files_late_fee` (`config_cnp.py`, `vlm_client_cnp.py`,
   `numea_search_by_edeces_id.py`) contiennent des identifiants en clair. Rien
   n'en est repris : ni mot de passe, ni DSN, ni nom d'hôte. `agent-edc` ne
   connaît que des variables d'environnement (§ A).

---

## 3 — Arborescence cible

```
agent-edc/
├── .env.example              # secrets et adresses (EDC_*, VLM_BASE_URL, clés Langfuse)
├── config.yaml               # réglages non sensibles, versionné (modèle, raisonnement, historique…)
├── .gitignore                # .env, .checkpoints/, logs/, .langgraph_api/
├── langgraph.json            # déclare le graphe au serveur LangGraph
├── pyproject.toml            # géré par uv — jamais édité à la main
├── README.md                 # démarrage express, renvoie vers docs/
├── docs/
│   └── ARCHITECTURE.md       # ← ce document
├── src/
│   └── agent_edc/
│       ├── __init__.py
│       ├── config.py         # sous-configs pydantic-settings : EDC_, VLM_, LANGFUSE_, AGENT_, HISTORY_ + config.yaml
│       ├── models.py         # EdcDossier, Event, Beneficiary, CaseFile (pydantic)
│       ├── observability.py  # sinks loguru + CallbackHandler Langfuse + décorateur traced_tool
│       ├── llm.py            # build_chat_model() : la seule fabrique de modèle
│       ├── prompts.py        # SYSTEM_PROMPT (français) et ses constantes
│       ├── snapshot.py       # fonctions PURES de lecture d'un CaseFile : filtres, tri, pagination, recherche, stats
│       ├── formatting.py     # rendu français compact d'un CaseFile et de ses fragments (le contrat « budget de tokens »)
│       ├── cli.py            # REPL Typer de mise au point en terminal
│       ├── edc/
│       │   ├── __init__.py
│       │   ├── connection.py   # edc_connection(), _lob_output_handler(), fetch_rows(), ensure_thick_mode()
│       │   ├── queries.py      # les 5 SQL à variables de liaison
│       │   ├── nomenclatures.py# ~300 codes EDC → libellés + registre NOMENCLATURES
│       │   ├── normalizers.py  # lignes Oracle → modèles ; PURES, testables sans Oracle ; stitch_bloc_note()
│       │   └── loader.py       # load_case_file() : les 5 requêtes dans UNE connexion
│       └── agent/
│           ├── __init__.py
│           ├── state.py        # AgentState (TypedDict) + réducteurs
│           ├── build.py        # assemblage du graphe ; expose `graph` pour langgraph.json
│           └── tools/
│               ├── __init__.py       # ALL_TOOLS : la liste liée au modèle
│               ├── dossier.py        # charger / rafraichir / resume / bloc_note
│               ├── events.py         # lister / chercher / detail / statistiques
│               ├── beneficiaries.py  # lister / detail / synthese_montants
│               └── nomenclature.py   # libelle_nomenclature
└── tests/
    ├── conftest.py                  # fixture CaseFile de référence + FakeChatModel
    ├── test_normalizers.py          # normalisation d'id, libellés, motifs, montants
    ├── test_bloc_note.py            # § 7.6 — la reconstitution du bloc-note
    ├── test_snapshot.py             # filtres, recherche, pagination, statistiques
    ├── test_formatting.py           # formats français compacts, troncatures
    ├── test_tools.py                # les 12 outils sur la fixture
    ├── test_graph.py                # boucle ReAct de bout en bout sur FakeChatModel
    ├── test_nomenclature_parity.py  # -m parity, § 2.4
    └── live/
        └── test_live_edc.py         # -m live, nécessite Oracle + wallet (§ 12.4)
```

### 3.1 Responsabilité de chaque module (une ligne)

| Module | Responsabilité |
|---|---|
| `config.py` | Lire l'environnement et rien d'autre ; aucune valeur par défaut secrète. |
| `models.py` | Le vocabulaire du domaine ; aucune E/S, aucun rendu. |
| `observability.py` | Journaux et traces ; **ne lève jamais**, même mal configuré. |
| `llm.py` | Construire le modèle de chat ; unique point de bascule de backend (§ 8). |
| `prompts.py` | Le texte du prompt système, isolé pour être relu par un métier. |
| `snapshot.py` | Répondre à une question sur un `CaseFile` en mémoire — **jamais de SQL ici**. |
| `formatting.py` | Transformer un résultat en texte français compact pour le modèle. |
| `cli.py` | Une boucle terminal pour déboguer sans interface web. |
| `edc/connection.py` | Ouvrir/fermer Oracle et rapatrier les CLOB en `str`. |
| `edc/queries.py` | Le SQL, et uniquement le SQL. |
| `edc/nomenclatures.py` | Les tables de codes, figées. |
| `edc/normalizers.py` | Lignes brutes → modèles ; **fonctions pures** (c'est ce qui rend 80 % du projet testable sans base). |
| `edc/loader.py` | Le seul endroit du projet qui parle à Oracle. |
| `agent/state.py` | La forme de l'état du fil et ses réducteurs. |
| `agent/build.py` | Le câblage du graphe ; aucune logique métier. |
| `agent/tools/*` | La surface exposée au modèle : signatures, descriptions françaises, garde-fous. |

### 3.2 Dépendances

Toujours via `uv add` — **jamais** d'édition manuelle de `pyproject.toml`.

```bash
uv add langchain langchain-openai langgraph langgraph-checkpoint-sqlite \
       langfuse oracledb pydantic pydantic-settings loguru typer rich
uv add --dev pytest ruff "langgraph-cli[inmem]"
```

Ce qui est délibérément **absent** par rapport à `deces-risk` : `pandas`,
`pyarrow`, `openpyxl` (pas de rapport tabulaire), `pymupdf` (pas de documents),
`jinja2` (un seul prompt, une constante suffit), `requests` (pas d'API HTTP).

---

## 4 — Le graphe et son état

### 4.1 La topologie : un ReAct minimal

```
  START ──► compact (no-op sauf compactage, § 4.6)
                    ┌───────────────────────────────────────────┐
                    │                                           │
            ───────►│  agent                                    │
                    │  modèle + 12 outils liés (bind_tools)      │
                    │  prompt système injecté en tête            │
                    └──────────────────┬────────────────────────┘
                                       │
                              tools_condition
                    ┌──────────────────┴──────────────────┐
                    │ le message contient des tool_calls   │ sinon
                    ▼                                      ▼
            ┌───────────────┐                            (END)
            │  tools        │  ToolNode(ALL_TOOLS)
            │               │  exécute les appels en parallèle
            └───────┬───────┘
                    │
                    └──────────────► retour inconditionnel vers « agent »
```

Deux nœuds, une arête conditionnelle, une arête de retour — plus une étape
`compact` en tête de tour, sans effet tant que le compactage est coupé (§ 4.6).

```python
def build_agent(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Build and compile the conversational EDC agent."""
    builder = StateGraph(AgentState)
    builder.add_node("compact", compact_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(ALL_TOOLS, handle_tool_errors=_tool_error_message))
    builder.add_edge(START, "compact")
    builder.add_edge("compact", "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)


#: Exposed to langgraph.json; the platform supplies its own persistence (§ 11.2).
graph = build_agent()
```

> **Pourquoi pas `create_react_agent` ?** Le préfabriqué impose son propre
> schéma d'état (`messages` + éventuellement un état structuré). Ici l'état
> porte trois champs supplémentaires (`edc_id`, `snapshot`, `loaded_at`) qui
> sont **le cœur de la conception** (§ 5) et que les outils doivent lire et
> écrire. Écrire les 10 lignes ci-dessus coûte moins cher que de contourner
> le préfabriqué, et rend la phase 2 (§ 13) plus facile.

### 4.2 Le nœud `agent`

```python
def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Call the model with the tools bound and the system prompt prepended."""
    settings = get_settings()
    summary = state.get("summary")
    system = SYSTEM_PROMPT + (SUMMARY_SECTION.format(summary=summary) if summary else "")
    history = stub_old_tool_results(
        messages_after(state["messages"], state.get("summary_until")),
        settings.history.tool_results_keep_turns,
    )
    model = get_chat_model().bind_tools(ALL_TOOLS, **tool_binding_kwargs(settings.vlm))
    response = model.invoke([SystemMessage(content=system), *history], config=config)
    return {"messages": [response]}
```

Quatre points :

- le prompt système est **reconstruit à chaque tour** et n'est jamais stocké
  dans `state["messages"]` — il reste ainsi modifiable sans invalider les fils
  déjà persistés ;
- `get_chat_model()` mémoïse l'instance au niveau du module (`functools.cache`) :
  reconstruire un `ChatOpenAI` à chaque tour recrée un client HTTP pour rien ;
- le modèle reçoit une **vue allégée** de l'historique (§ 4.6) ; l'état, lui,
  n'est jamais amputé ;
- `parallel_tool_calls: false` (config.yaml) limite le modèle à un appel
  d'outil par tour, ce qui règle structurellement le piège du § 4.5.

### 4.3 Le schéma d'état

```python
class AgentState(TypedDict):
    """State carried by one conversation thread (one dossier)."""

    messages: Annotated[list[AnyMessage], add_messages]
    edc_id: str | None
    snapshot: CaseFile | None
    loaded_at: datetime | None
```

| Champ | Contenu | Réducteur | Pourquoi ce réducteur |
|---|---|---|---|
| `messages` | l'historique complet du fil | `add_messages` | Réducteur additif standard : il concatène, dédoublonne par `id` et gère le remplacement d'un message réédité. |
| `edc_id` | l'id normalisé à 8 chiffres du dossier chargé | *défaut* (remplacement) | Un fil = un dossier ; la dernière valeur écrite fait foi. |
| `snapshot` | le `CaseFile` complet en mémoire | *défaut* (remplacement) | Un `refresh` doit **remplacer** l'instantané, pas s'y ajouter. Un réducteur additif serait ici un bug. |
| `loaded_at` | horodatage du chargement | *défaut* (remplacement) | Sert à calculer l'âge affiché dans chaque résumé (§ 5.4). |

> **Le réducteur par défaut de LangGraph est le remplacement** : une clé
> renvoyée par un nœud écrase la précédente. Seul `messages` est annoté, parce
> que seul lui doit s'accumuler. Un débutant est souvent tenté d'annoter tous
> les champs « pour faire pareil » — ce serait ici une erreur nette.

`AgentState` est un `TypedDict` et non un modèle pydantic : c'est ce que
`Command(update=...)` et les réducteurs manipulent le plus naturellement, et
cela évite une revalidation pydantic de tout l'instantané à chaque *superstep*.
Les **valeurs**, elles, restent des modèles pydantic (`CaseFile`), donc typées
et validées au chargement.

### 4.4 Comment les outils atteignent l'instantané

Par `InjectedState` : l'argument est rempli par LangGraph, **il n'apparaît pas
dans le schéma envoyé au modèle**, et le modèle ne peut donc ni le fabriquer ni
le falsifier.

```python
@tool("resume_dossier", parse_docstring=True)
def get_dossier_summary(state: Annotated[AgentState, InjectedState]) -> str:
    """Donne la fiche de synthèse du dossier actuellement chargé. …"""
```

Les outils qui **écrivent** dans l'état (`charger_dossier`,
`rafraichir_dossier`) renvoient un `Command` et reçoivent en plus
l'identifiant de l'appel, lui aussi injecté :

```python
@tool("charger_dossier", parse_docstring=True)
def load_dossier(
    edc_id: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    ...
    return Command(
        update={
            "edc_id": normalized_id,
            "snapshot": case_file,
            "loaded_at": now,
            "messages": [ToolMessage(summary_text, tool_call_id=tool_call_id)],
        }
    )
```

Le `ToolMessage` est **obligatoire** : sans lui, l'appel d'outil resterait sans
réponse et le prochain appel au modèle échouerait sur un historique incohérent.

### 4.5 Le piège des appels parallèles (à connaître avant de coder)

`InjectedState` fournit l'état **tel qu'il était au début du pas `tools`**. Si
le modèle émet dans un même message `charger_dossier("01234567")` **et**
`resume_dossier()`, le second s'exécute sur un état où `snapshot` vaut encore
`None` et répond « aucun dossier chargé » — alors que le premier vient de le
charger.

Deux garde-fous, complémentaires :

1. **`charger_dossier` renvoie déjà le résumé** dans son propre `ToolMessage`.
   Le modèle n'a donc **jamais** de raison d'enchaîner les deux ; c'est la
   mitigation principale, et elle est structurelle.
2. Le prompt système contient une règle explicite : *« charge le dossier seul,
   attends le résultat, puis appelle les autres outils »* (§ 9.4).
3. Tout outil de lecture appelé sans instantané répond, en français, par une
   phrase qui **indique quoi faire** — pas par une erreur :
   *« Aucun dossier n'est chargé. Appelle d'abord `charger_dossier` avec
   l'identifiant à 8 chiffres. »* Le modèle se corrige alors au tour suivant.

### 4.6 Historique et compactage

Un fil long finit par dépasser le contexte du modèle : chaque bloc-note ou
détail d'événement lu reste dans l'historique. Deux mécanismes, réglés dans la
section `history` de `config.yaml`, agissent **uniquement sur la vue envoyée
au modèle** (`agent/history.py`) — `messages` reste complet, donc Agent Chat UI
et la reprise d'un fil montrent toute la conversation.

1. **Rappels à la place des vieux résultats d'outils** (toujours actif,
   `tool_results_keep_turns`, défaut 3). Au-delà des N derniers tours, le
   contenu d'un `ToolMessage` est remplacé par « [Résultat antérieur retiré du
   contexte — rappelle « outil » si besoin.] ». Les outils lisent en mémoire :
   un rappel ne coûte rien.
2. **Compactage** (optionnel, `compaction: true`). En tête de tour, le nœud
   `compact` estime la taille du contexte (usage renvoyé par vLLM sur la
   dernière réponse, sinon caractères/3). Au-delà de
   `compaction_trigger_tokens`, les tours précédant les
   `compaction_keep_turns` derniers sont résumés par le modèle, **raisonnement
   coupé**, dans un span `compactage`. Le résumé est stocké dans l'état
   (`summary`, `summary_until`) et injecté dans le prompt système ; il se
   cumule d'un compactage à l'autre. Il garde dates, références EVT,
   bénéficiaires, montants et constats, mais **aucun extrait de bloc-note** :
   le prompt rappelle qu'un résumé ne se cite pas.

Les découpes tombent toujours sur un `HumanMessage` : un `tool_call` n'est
jamais séparé de son `ToolMessage`. Un compactage en échec est journalisé et
ignoré.

---

## 5 — Stratégie d'accès aux données (la décision structurante)

### 5.1 Le problème

Une conversation utile sur un dossier, c'est facilement **15 à 30 appels
d'outils** : lister, chercher un mot, ouvrir trois événements, vérifier un
bénéficiaire, revenir à la chronologie. Si chaque outil ouvrait une connexion
Oracle, on paierait :

- l'ouverture de connexion (mode *thick*, wallet) : ~**0,3 à 1 s** par appel ;
- une requête EDC dont les jointures ramènent les CLOB : **0,5 à 3 s** ;
- soit **30 s à 2 min** d'attente cumulée sur une conversation, pour relire
  **les mêmes lignes** trente fois.

C'est le mauvais modèle de coût : les données d'un dossier tiennent
confortablement en mémoire (un très gros dossier ≈ 500 événements ≈ quelques
centaines de kilo-octets une fois `raw` supprimé, § 2.3 b).

### 5.2 Le choix : un chargement groupé, puis de la mémoire

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  TOUR 1 — le gestionnaire mentionne « 01234567 »                     │
  └──────────────────────────────────────────────────────────────────────┘
        │
        │ charger_dossier("01234567")
        ▼
  ┌─────────────────────────────────────────────┐
  │  load_case_file()                           │      UNE SEULE FOIS
  │  ┌───────────────────────────────────────┐  │      PAR CONVERSATION
  │  │ ensure_thick_mode()  (1×/processus)   │  │
  │  │ with edc_connection(config) as conn:  │  │      ~1 à 4 s
  │  │    QUERY_DOSSIER                      │  │
  │  │    QUERY_EVENTS                       │  │
  │  │    QUERY_BENEFICIARIES                │  │
  │  │    QUERY_AMOUNTS_PAID                 │  │
  │  │    QUERY_AMOUNTS_REMAINING            │  │
  │  └───────────────────────────────────────┘  │
  │  → normalisation → CaseFile                 │
  └────────────────────┬────────────────────────┘
                       │
                       ▼
        state["snapshot"] = CaseFile   ────────────┐
        state["loaded_at"] = now                   │  persisté par le
        ToolMessage = résumé français compact      │  checkpointer (§ 11.2)
                                                   │
  ┌────────────────────────────────────────────────┴─────────────────────┐
  │  TOURS 2..n — toutes les autres questions                            │
  └──────────────────────────────────────────────────────────────────────┘
        │
        │ lister_evenements / chercher_evenements / detail_evenement /
        │ statistiques_chronologie / lister_beneficiaires / …
        ▼
  ┌─────────────────────────────────────────────┐
  │  snapshot.py — filtres, tri, recherche,      │      ~1 ms
  │  pagination, statistiques  (Python pur)      │      ZÉRO SQL
  └─────────────────────────────────────────────┘
        │
        │ rafraichir_dossier()  ← uniquement sur demande explicite
        └──────────────────────► retour à load_case_file()
```

Le chargement lui-même :

```python
def load_case_file(config: EDCConfig, edc_id: str) -> CaseFile | None:
    """Run every EDC query for one dossier inside a single connection.

    Args:
        config: Oracle settings for the EDC database.
        edc_id: Normalized 8-digit dossier id.

    Returns:
        The in-memory snapshot, or None when the dossier does not exist.
    """
    with edc_connection(config) as connection:
        dossier_rows = fetch_rows(connection, QUERY_DOSSIER, {"id_dossier": edc_id})
        if not dossier_rows:
            return None
        event_rows = fetch_rows(connection, QUERY_EVENTS, {"id_dossier": edc_id})
        beneficiary_rows = fetch_rows(connection, QUERY_BENEFICIARIES, {"id_dossier": edc_id})
        paid_rows = fetch_rows(connection, QUERY_AMOUNTS_PAID, {"id_dossier": edc_id})
        remaining_rows = fetch_rows(connection, QUERY_AMOUNTS_REMAINING, {"id_dossier": edc_id})

    return CaseFile(
        edc_id=edc_id,
        dossier=normalize_dossier(edc_id, dossier_rows[0]),
        events=sorted(
            (normalize_edc_event(row) for row in event_rows),
            key=lambda e: (e.date is None, e.date or datetime.max),
        ),
        beneficiaries=normalize_beneficiaries(
            beneficiary_rows,
            aggregate_amounts(paid_rows, "MONTANTPAYE"),
            aggregate_amounts(remaining_rows, "MONTANTRESTANTAPAYER"),
        ),
    )
```

Points à respecter à l'implémentation :

- **La sortie anticipée** (`if not dossier_rows: return None`) évite quatre
  requêtes inutiles sur un id inexistant — le cas le plus fréquent de faute de
  frappe.
- Le tri chronologique est fait **une fois au chargement** ; tous les outils
  supposent ensuite `snapshot.events` trié, ce qui rend la pagination stable
  entre deux appels.
- La fonction est la **seule** du projet à appeler Oracle. Les tests n'ont donc
  qu'un point à remplacer (§ 12).

### 5.3 Initialisation du mode *thick*, une fois par processus

`oracledb.init_oracle_client()` **doit** être appelé au plus une fois par
processus ; un second appel lève. Le garde-fou de `deces-risk` est porté tel
quel :

```python
_thick_mode_initialized = False


def ensure_thick_mode() -> None:
    """Initialize the Oracle thick client once per process (wallet auth)."""
    global _thick_mode_initialized
    if not _thick_mode_initialized:
        oracledb.init_oracle_client()
        _thick_mode_initialized = True
```

C'est d'autant plus important ici que le serveur LangGraph est un processus
**long** qui sert plusieurs fils : sans ce drapeau, la deuxième conversation
planterait. Le premier chargement paie l'initialisation (~0,5 s), les suivants
non.

### 5.4 Le résumé compact injecté au chargement

`charger_dossier` ne renvoie pas « dossier chargé » : il renvoie **la fiche**,
pour que le modèle dispose immédiatement du contexte sans un second aller-retour
(et, accessoirement, pour désamorcer le piège du § 4.5).

```
Dossier 01234567 — état « En cours » · réseau Caisse d'Épargne
Référence interne 4412887 · créé le 12/03/2024 · date d'effet 05/03/2024
Événements : 137, du 12/03/2024 au 02/09/2026 (dernier il y a 16 jours)
Bénéficiaires : 3 (dont 1 présumé)
Montants : payé 42 300,00 € · restant à payer 12 000,00 €
Non soldés : « MARTIN Claire » (restant 12 000,00 €)
Bloc-note du dossier : 1 842 caractères — lire avec « bloc_note_dossier ».

5 derniers événements :
  02/09/2026 · EVT-9912 · Courrier (entrant ou sortant) — Arrivée courrier · Notaire · « reçu acte de notoriété, transmis au service… »
  21/08/2026 · EVT-9880 · Evénement standard — Relance manuelle de demande de pièces · « 3e relance, sans réponse depuis le… »
  14/07/2026 · EVT-9801 · Alerte — Dossier en attente > 90 jours
  02/07/2026 · EVT-9788 · Evénement standard — Ordonnancement d'une prestation
  28/06/2026 · EVT-9771 · Communication — Réception com. téléphonique · Bénéficiaire · « appel de Mme MARTIN, demande où en est… »

Instantané chargé le 18/09/2026 à 14:02 (il y a 0 min).
```

Choix de contenu, tous justifiés par la question *« ce dossier est-il bien
géré ? »* :

| Bloc | Pourquoi il est dans le résumé |
|---|---|
| état + réseau | détermine ce qu'on est en droit d'attendre (les circuits Trésor/Poste/CE diffèrent) |
| dates de création et d'effet | l'ancienneté est le premier signal de retard |
| nombre d'événements + bornes + **fraîcheur du dernier acte** | « 16 jours » ou « 400 jours » change tout le diagnostic |
| nombre de bénéficiaires, dont présumés | un bénéficiaire présumé jamais qualifié est une anomalie classique |
| payé / restant | le solde restant est l'assiette d'éventuels intérêts de retard |
| **liste nominative des non soldés** | répond d'emblée à la moitié des questions posées |
| taille du bloc-note + nom de l'outil pour le lire | évite que le modèle croie l'avoir déjà lu |
| N derniers événements | donne le « où on en est » sans appeler d'outil |
| âge de l'instantané | rend la fraîcheur visible en permanence (§ 5.5) |

`N` vaut 5 par défaut (`agent.summary_events`). Le résumé complet tient en
**~400 tokens** — à comparer aux ~15 000 tokens qu'un dépôt brut de 137
événements coûterait.

### 5.5 Le compromis de fraîcheur, assumé et documenté

**Le fait.** L'instantané est une photographie. Si un gestionnaire saisit un
événement dans EDC pendant la conversation, l'agent ne le voit pas.

**Pourquoi c'est acceptable.**

- Une conversation dure quelques minutes ; un dossier décès évolue à l'échelle
  du jour ou de la semaine.
- L'agent répond à des questions **rétrospectives** (« qu'a-t-on fait ? »,
  « a-t-on relancé ? ») : ce sont des faits passés, que le rafraîchissement ne
  changerait pas.
- Le coût inverse — relire Oracle 30 fois — est certain, alors que le risque
  d'obsolescence est occasionnel.

**Comment il est rendu visible.**

1. Chaque sortie d'outil qui dépend de l'instantané se termine par une ligne
   d'âge dès que celui-ci dépasse `agent.stale_after_minutes` (défaut : 30) :
   *« Instantané chargé il y a 47 min — utilise `rafraichir_dossier` si la
   fraîcheur importe. »* En deçà, la ligne n'apparaît que dans le résumé, pour
   ne pas gaspiller de tokens.
2. Le prompt système énumère les cas où le rafraîchissement **s'impose** (§ 9.4).

**Quand rafraîchir — la règle.**

| Situation | Rafraîchir ? |
|---|---|
| L'utilisateur dit avoir saisi / modifié quelque chose à l'instant | **Oui, systématiquement** |
| La question porte sur « aujourd'hui », « maintenant », « le point le plus récent » | **Oui** |
| L'instantané a plus de 30 min et la question porte sur l'état courant | **Oui** |
| L'utilisateur conteste un chiffre (montant, nombre d'événements) | **Oui** — lever le doute coûte 2 s |
| Question sur un événement de 2024, une relance passée, un historique | Non |
| Reformulation d'une question déjà traitée | Non |

`rafraichir_dossier` réexécute exactement `load_case_file()` et **remplace**
l'instantané (§ 4.3). Il renvoie le résumé et, en tête, ce qui a changé :
*« 2 nouveaux événements depuis le chargement précédent (le plus récent :
18/09/2026 · EVT-9930). »* Ce delta est calculé en mémoire, par différence des
`event_id`.

### 5.6 Ce que le checkpointer stocke

L'instantané est persisté avec l'état du fil, donc **il survit à un
redémarrage** : rouvrir une conversation de la veille ne rejoue pas les
requêtes Oracle.

Une inquiétude légitime : « écrit-on 300 Ko à chaque tour ? » Non — LangGraph
écrit les **canaux modifiés** à chaque *superstep*. Le canal `snapshot` n'est
écrit qu'au chargement et au rafraîchissement ; les tours suivants n'écrivent
que `messages`. C'est précisément pour cela que `snapshot` a un réducteur de
remplacement et non un réducteur additif.

---

## 6 — Catalogue d'outils

### 6.1 Conventions communes

**Nommage bilingue.** Le nom d'outil fait partie du prompt : il est donc en
français. La fonction Python, elle, suit la règle du projet (code en anglais).
D'où le patron systématique :

```python
@tool("lister_evenements", parse_docstring=True)
def list_events(...) -> str:
    """Liste les événements du dossier, du plus récent au plus ancien. …"""
```

De même, **les docstrings des outils sont en français** : elles *sont* la
description vue par le modèle. C'est la seule exception à la règle « docstrings
en anglais », et elle est délibérée. Le format reste Google (`Args:`), parsé par
`parse_docstring=True` pour produire la description de chaque argument dans le
schéma. **Seuls les arguments visibles du modèle sont documentés** ; `state` et
`tool_call_id` sont injectés et absents du schéma.

**Contrat de retour.** Tout outil renvoie `str` (du texte français compact),
sauf les deux outils écrivains qui renvoient `Command`. Jamais de `dict`, jamais
de JSON — voir § 6.8.

**Contrat d'erreur.** Un outil **ne lève pas**. Il renvoie une phrase française
qui dit ce qui manque *et* ce qu'il faut faire :

| Cas | Réponse |
|---|---|
| Aucun instantané en état | « Aucun dossier n'est chargé. Appelle d'abord `charger_dossier` avec l'identifiant à 8 chiffres. » |
| Id mal formé | « Identifiant invalide : « 123ABC ». Un identifiant E-décès comporte 8 chiffres. » |
| Dossier introuvable | « Aucun dossier E-décès ne porte l'identifiant 01234567. Vérifie le numéro auprès du gestionnaire. » |
| EDC injoignable | « La base E-décès est momentanément inaccessible ; je ne peux pas charger le dossier. » (le détail technique part dans les journaux, **pas** vers le modèle) |
| Référence inconnue (événement, bénéficiaire) | « Aucun événement EVT-9999 dans ce dossier. Utilise `lister_evenements` pour voir les références disponibles. » |
| Page hors bornes | « Le dossier compte 137 événements ; le décalage 500 est hors bornes. » |

En dernier recours, `ToolNode(..., handle_tool_errors=_tool_error_message)`
transforme toute exception non prévue en message français générique : une
exception ne doit jamais interrompre la conversation.

**Bornes de pagination.** `limite` est ramenée dans `[1, agent.max_page_size]`
(défaut 50) sans erreur : un modèle qui demande 500 obtient 50 et une phrase le
lui disant. `decalage` négatif est ramené à 0.

**Troncature.** Dans les *listes*, le détail d'un événement est coupé à 160
caractères suivis de `…`, avec le rappel de l'outil qui donne le texte entier.
Dans `detail_evenement` et `bloc_note_dossier`, **rien n'est jamais tronqué**
(§ 7.1).

### 6.2 Vue d'ensemble

| # | Nom (vu par le modèle) | Fonction Python | Écrit l'état | Coût |
|---|---|---|---|---|
| 1 | `charger_dossier` | `load_dossier` | ✅ | Oracle |
| 2 | `rafraichir_dossier` | `refresh_dossier` | ✅ | Oracle |
| 3 | `resume_dossier` | `get_dossier_summary` | — | mémoire |
| 4 | `bloc_note_dossier` | `get_dossier_note` | — | mémoire |
| 5 | `lister_evenements` | `list_events` | — | mémoire |
| 6 | `chercher_evenements` | `search_events` | — | mémoire |
| 7 | `detail_evenement` | `get_event` | — | mémoire |
| 8 | `statistiques_chronologie` | `get_timeline_stats` | — | mémoire |
| 9 | `lister_beneficiaires` | `list_beneficiaries` | — | mémoire |
| 10 | `detail_beneficiaire` | `get_beneficiary` | — | mémoire |
| 11 | `synthese_montants` | `get_amounts_summary` | — | mémoire |
| 12 | `libelle_nomenclature` | `lookup_nomenclature` | — | aucun (tables figées) |

### 6.3 Dossier : chargement, rafraîchissement, synthèse, bloc-note

#### 1. `charger_dossier`

```python
@tool("charger_dossier", parse_docstring=True)
def load_dossier(
    edc_id: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Charge un dossier E-décès en mémoire et renvoie sa fiche de synthèse.

    À appeler UNE SEULE FOIS, dès que le gestionnaire mentionne un identifiant.
    Tous les autres outils travaillent ensuite sur ce dossier sans nouvel accès
    à la base. Appelle cet outil seul, puis attends son résultat avant tout
    autre appel.

    Args:
        edc_id: Identifiant du dossier E-décès, 8 chiffres (un identifiant à
            7 chiffres est accepté, le zéro de tête est restauré).
    """
```

**Retour** — `Command` mettant à jour `edc_id`, `snapshot`, `loaded_at`, plus un
`ToolMessage` contenant le résumé du § 5.4.
**Limites** — aucune ; le chargement est complet par construction.
**Erreurs** — id invalide, dossier introuvable, EDC injoignable : phrase
française, l'état reste inchangé (`Command(update={"messages": [...]})` seul).
**Idempotence** — si `state["edc_id"]` vaut déjà cet id et que l'instantané a
moins de `agent.stale_after_minutes`, l'outil **ne relit pas Oracle** : il
renvoie le résumé existant en le signalant (« déjà chargé il y a 4 min »). Ce
cas est marqué `cache_hit=True` dans la trace (§ 10.4).

#### 2. `rafraichir_dossier`

```python
@tool("rafraichir_dossier", parse_docstring=True)
def refresh_dossier(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Relit le dossier dans la base E-décès et remplace la version en mémoire.

    À utiliser quand la fraîcheur compte : le gestionnaire vient de saisir
    quelque chose, la question porte sur la situation d'aujourd'hui, ou les
    données en mémoire datent de plus d'une demi-heure. Inutile pour une
    question sur des faits passés.
    """
```

**Retour** — même `Command` que `charger_dossier`, précédé du **delta** :
nombre d'événements apparus depuis le chargement précédent, avec la référence
et la date du plus récent ; « aucun changement » le cas échéant.
**Erreurs** — si la relecture échoue, **l'ancien instantané est conservé** et
l'outil répond : « La relecture a échoué ; je continue avec l'instantané du
18/09/2026 à 14:02. » Perdre un instantané valide sur une erreur réseau serait
une régression pour l'utilisateur.

#### 3. `resume_dossier`

```python
@tool("resume_dossier", parse_docstring=True)
def get_dossier_summary(state: Annotated[AgentState, InjectedState]) -> str:
    """Redonne la fiche de synthèse du dossier chargé : état, réseau, dates,
    nombre d'événements et de bénéficiaires, montants payés et restants,
    bénéficiaires non soldés, et les derniers événements.

    Utile pour se resituer au milieu d'une conversation longue. Ne relit pas
    la base.
    """
```

**Retour** — le texte du § 5.4 (~400 tokens).
**Erreurs** — message « aucun dossier chargé ».

#### 4. `bloc_note_dossier`

```python
@tool("bloc_note_dossier", parse_docstring=True)
def get_dossier_note(state: Annotated[AgentState, InjectedState]) -> str:
    """Renvoie le bloc-note global du dossier, en intégralité et sans coupure.

    C'est le commentaire libre tenu par les gestionnaires : la source la plus
    riche pour comprendre ce qui a été fait, tenté ou décidé sur le dossier.
    À lire systématiquement avant de porter un jugement sur la qualité de la
    gestion.
    """
```

**Retour** — le texte brut, **jamais tronqué**, précédé d'une ligne
« Bloc-note du dossier 01234567 (1 842 caractères) : ». Si le bloc-note est
vide : « Ce dossier n'a pas de bloc-note global. » — information utile en soi,
et à ne pas confondre avec une erreur.
**Limites** — aucune. Un bloc-note de 20 000 caractères (~5 000 tokens) est
renvoyé entier ; c'est un coût accepté, cf. § 7.1.

### 6.4 Chronologie : lister, chercher, détailler, mesurer

#### 5. `lister_evenements`

```python
@tool("lister_evenements", parse_docstring=True)
def list_events(
    state: Annotated[AgentState, InjectedState],
    decalage: int = 0,
    limite: int = 20,
    date_min: str | None = None,
    date_max: str | None = None,
    type_evenement: str | None = None,
    beneficiaire_id: str | None = None,
    ordre: Literal["recent", "ancien"] = "recent",
) -> str:
    """Liste les événements du dossier, avec filtres et pagination.

    Args:
        decalage: Nombre d'événements à sauter (0 pour la première page).
        limite: Nombre d'événements à renvoyer, 50 au maximum.
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
```

**Retour** — un en-tête de position puis une ligne par événement :

```
Événements 1 à 20 sur 137 (filtre : type « alerte »).
  02/09/2026 · EVT-9912 · Courrier (entrant ou sortant) — Arrivée courrier · Notaire · « reçu acte de notoriété, transmis au service succession pour contrôle de la dévolution avant… »
  21/08/2026 · EVT-9880 · Evénement standard — Relance manuelle de demande de pièces · « 3e relance, sans réponse depuis le 12/06 »
  …
Page suivante : decalage=20.
```

**Format d'une ligne** : `date · référence · libellé · [motif] · [interlocuteur] · « détail tronqué à 160 car. »`. Les champs absents sont
omis — pas de `motif: None` qui consomme des tokens pour rien.
**Pagination** — `limite` bornée à 50 ; l'en-tête donne toujours le total
filtré, et le pied indique le `decalage` suivant lorsqu'il reste des résultats.
Ordre par défaut « récent » : les questions portent le plus souvent sur ce qui
vient de se passer.
**Erreurs** — date mal formée : « Date illisible : « 2024-07-14 ». Utilise le
format JJ/MM/AAAA. » (le filtre n'est **pas** silencieusement ignoré : un filtre
ignoré produirait une réponse fausse sans que personne ne s'en aperçoive).
Filtre ne renvoyant rien : « Aucun événement ne correspond (137 événements au
total). » avec le rappel des filtres appliqués.

#### 6. `chercher_evenements`

```python
@tool("chercher_evenements", parse_docstring=True)
def search_events(
    requete: str,
    state: Annotated[AgentState, InjectedState],
    limite: int = 20,
) -> str:
    """Cherche un mot ou une expression dans tout le contenu des événements.

    La recherche porte sur le libellé, le détail (bloc-note de l'événement),
    le motif et la nature de l'interlocuteur. Elle ignore la casse et les
    accents. C'est l'outil à privilégier pour retrouver une trace précise :
    « notaire », « relance », « succession vacante », un nom propre.

    Args:
        requete: Le mot ou l'expression à chercher.
        limite: Nombre de résultats à renvoyer, 50 au maximum.
    """
```

**Retour** — le nombre total de correspondances, puis les `limite` plus
récentes, au même format que `lister_evenements` mais avec un extrait
**centré sur la correspondance** (±80 caractères) plutôt que le début du
détail : c'est ce qui rend la citation directement exploitable.

```
12 événements contiennent « notaire ». Les 12 plus récents :
  02/09/2026 · EVT-9912 · Courrier — Arrivée courrier · Notaire · « …reçu acte de notoriété du NOTAIRE Me Dupont, transmis au service… »
```

**Implémentation** — normalisation NFKD + suppression des diacritiques des deux
côtés, puis `in`. Pas de recherche floue, pas d'index : sur 500 événements en
mémoire c'est instantané, et une recherche approximative rendrait les citations
invérifiables.
**Erreurs** — requête vide ou de moins de 2 caractères : « Précise un terme
d'au moins deux caractères. »

#### 7. `detail_evenement`

```python
@tool("detail_evenement", parse_docstring=True)
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
            listes (par exemple « EVT-9912 »).
    """
```

**Retour** — un bloc intégral :

```
Événement EVT-9912
Date de création : 02/09/2026 · dernière mise à jour : 03/09/2026
Type : Courrier (entrant ou sortant) — Arrivée courrier  (codes TYPEEVT=1, SOUSTYPEEVT=1.1)
Interlocuteur : Notaire
Motif : —
Rattaché au bénéficiaire : 88412 (« MARTIN Claire »)
Bloc-note :
reçu acte de notoriété du notaire Me Dupont, transmis au service succession pour contrôle de la dévolution avant ordonnancement ; relance faite par téléphone le 28/08, la réponse était attendue pour fin août.
Commentaire (XML) :
pièce classée au dossier le 03/09.
```

Les **codes bruts sont affichés à côté du libellé** : c'est ce qui permet à
l'agent d'expliquer un code si on le lui demande, sans jamais l'inventer
(§ 9.5).
**Limites** — aucune troncature, jamais.
**Erreurs** — référence inconnue : message avec renvoi vers
`lister_evenements`.

#### 8. `statistiques_chronologie`

```python
@tool("statistiques_chronologie", parse_docstring=True)
def get_timeline_stats(state: Annotated[AgentState, InjectedState]) -> str:
    """Donne la forme de la chronologie : volume, période couverte,
    répartition par type, et surtout les périodes sans aucun événement.

    C'est l'outil le plus direct pour repérer un dossier laissé en sommeil :
    un intervalle de plusieurs mois sans le moindre acte est le premier signe
    d'une gestion défaillante. Ne remplace pas la lecture des événements, mais
    indique où regarder.
    """
```

**Retour** :

```
137 événements, du 12/03/2024 au 02/09/2026 (904 jours).
Répartition par type : Evénement standard 78 · Courrier (entrant ou sortant) 41 · Alerte 12 · Communication 6
Intervalle moyen entre deux événements : 6,6 jours.
Plus long intervalle sans événement : 217 jours, du 14/07/2024 (EVT-4102) au 16/02/2025 (EVT-5533).
Autres intervalles de plus de 60 jours :
  91 jours, du 03/11/2025 (EVT-8110) au 02/02/2026 (EVT-8377)
  74 jours, du 12/04/2026 (EVT-9001) au 25/06/2026 (EVT-9150)
Dernier événement il y a 16 jours.
```

Chaque intervalle est **borné par les deux références d'événements**, pour que
l'agent puisse enchaîner directement sur `detail_evenement` et expliquer
*pourquoi* le dossier s'est arrêté (mise en attente motivée ? oubli ?).
**Limites** — seuil des « autres intervalles » fixé à 60 jours, 5 au maximum.
**Erreurs** — dossier sans événement daté : « Aucun événement daté ; les
statistiques chronologiques ne s'appliquent pas. »

### 6.5 Bénéficiaires et montants

#### 9. `lister_beneficiaires`

```python
@tool("lister_beneficiaires", parse_docstring=True)
def list_beneficiaries(state: Annotated[AgentState, InjectedState]) -> str:
    """Liste tous les bénéficiaires connus du dossier, avec leur statut de
    paiement.

    Un bénéficiaire « présumé » n'a pas encore été qualifié : c'est une étape
    de gestion qui reste à faire. Un bénéficiaire dont le restant à payer est
    supérieur à zéro n'est pas soldé.
    """
```

**Retour** :

```
3 bénéficiaires (dont 1 présumé) :
  88412 · MARTIN Claire · payé 30 300,00 € · restant 12 000,00 € · NON SOLDÉ · 24 événements rattachés
  88413 · MARTIN Paul · payé 12 000,00 € · restant 0,00 € · soldé · 9 événements rattachés
  88414 · (identité inconnue) · présumé · aucun montant · 2 événements rattachés
```

**Limites** — pas de pagination : un dossier compte quelques bénéficiaires, pas
des centaines. Au-delà de 50, la liste est coupée avec un compteur explicite
(garde-fou, pas fonctionnalité).
**Nota** — « identité inconnue » n'est pas une erreur : la chaîne
`ESDROLE → ESDPERSONNE → ESDPERSONNEPHYSIQUE` est en jointure externe, et un
bénéficiaire présumé n'a souvent aucune personne rattachée. Le distinguer d'un
échec technique évite au modèle de conclure à un bug.

#### 10. `detail_beneficiaire`

```python
@tool("detail_beneficiaire", parse_docstring=True)
def get_beneficiary(
    beneficiaire_id: str,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Donne la fiche d'un bénéficiaire : identité, statut, montants, et les
    derniers événements qui le concernent.

    Args:
        beneficiaire_id: Référence du bénéficiaire donnée par
            « lister_beneficiaires » (par exemple « 88412 »). Un nom de famille
            est également accepté s'il ne correspond qu'à un seul bénéficiaire.
    """
```

**Retour** — identité, présumé ou non, montants, puis les **10 derniers
événements rattachés** (au format des listes, détails tronqués), et le rappel
que `lister_evenements(beneficiaire_id=…)` donne la suite.
**Recherche par nom** — tolérée parce que le gestionnaire parle en noms, pas en
références. Si le nom correspond à plusieurs bénéficiaires, l'outil ne choisit
pas : « « MARTIN » correspond à 2 bénéficiaires : 88412 (Claire), 88413 (Paul).
Précise la référence. »
**Erreurs** — référence inconnue, renvoi vers `lister_beneficiaires`.

#### 11. `synthese_montants`

```python
@tool("synthese_montants", parse_docstring=True)
def get_amounts_summary(state: Annotated[AgentState, InjectedState]) -> str:
    """Donne la situation financière du dossier : total payé, total restant à
    payer, et la liste des bénéficiaires qui ne sont pas intégralement payés.

    Le restant à payer est l'assiette sur laquelle se calculeraient
    d'éventuels intérêts de retard : c'est l'indicateur central pour juger de
    l'urgence d'un dossier.
    """
```

**Retour** :

```
Total payé : 42 300,00 €
Total restant à payer : 12 000,00 €
Bénéficiaires non soldés (1 sur 3) :
  88412 · MARTIN Claire · restant 12 000,00 €
Bénéficiaires sans aucun montant renseigné (1) :
  88414 · (identité inconnue) · présumé
Montants issus des ordonnancements EDC (listeOrdonnancements), convertis des centimes en euros.
```

La dernière ligne — la **provenance** — est là pour que l'agent ne présente
jamais ces montants comme la valeur des contrats : ce sont des ordonnancements
EDC, et le périmètre des contrats n'est pas chargé en v1 (§ 1.4).
**Distinction importante** — « restant à payer = 0 » (soldé) et « aucun montant
renseigné » (rien d'ordonnancé) sont deux situations **différentes**, et la
seconde est souvent l'anomalie recherchée. Les confondre serait le bug
fonctionnel le plus coûteux de cette section.

### 6.6 Nomenclatures

#### 12. `libelle_nomenclature`

```python
@tool("libelle_nomenclature", parse_docstring=True)
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
        code: Le code à traduire, par exemple « 5 », « 1.1 » ou « M8 ».
    """
```

**Retour** — `Type d'événement · code « 5 » → « Alerte »`.
**Sans instantané** — cet outil ne lit pas l'état : les tables sont des
constantes. Il est donc appelable avant tout chargement.
**Erreurs** — code absent : « Le code « 77 » n'existe pas dans la nomenclature
des motifs de fin de traitement (10 codes connus : 1 à 10). » Le fait de
**renvoyer les codes valides** évite la boucle où le modèle réessaie au hasard.
**Exploration** — `code="*"` renvoie la table entière (au plus 300 lignes) ;
utile quand le gestionnaire demande « quels motifs d'attente existent ? ».

### 6.7 Pourquoi ces frontières et pas d'autres

| Décision | Justification |
|---|---|
| **12 outils, pas 4** | Un outil « fourre-tout » (`interroger_dossier(question)`) déplacerait la logique de filtrage dans le modèle, où elle est lente et non testable. Des outils étroits sont déterministes, testables un par un, et lisibles dans une trace. |
| **12 outils, pas 30** | Un outil par colonne EDC noierait le modèle : au-delà d'une quinzaine d'outils, le taux d'erreur de sélection augmente nettement. Les filtres sont donc des **paramètres** (`type_evenement`, `beneficiaire_id`), pas des outils distincts. |
| **Lister et chercher séparés** | Deux intentions différentes : « montre-moi la suite » (parcours, pagination) et « trouve ce mot » (ciblage, extrait centré). Les fusionner obligerait à un paramètre « mode » que le modèle choisirait mal. |
| **Détailler séparé de lister** | C'est la frontière **troncature / pas de troncature**. Elle est explicite et protège le bloc-note (§ 7.1). |
| **Statistiques séparées** | Répond à « ce dossier est-il bien géré ? » sans lire un seul événement, pour quelques dizaines de tokens. C'est l'outil au meilleur rapport signal/coût du catalogue. |
| **Montants séparés de la liste des bénéficiaires** | La question financière est posée seule au moins aussi souvent que la question nominative ; et la distinction « soldé » / « sans montant » mérite son propre rendu. |
| **Charger et rafraîchir séparés** | Deux intentions, deux descriptions, deux comportements d'erreur. Un paramètre `force=True` sur `charger_dossier` serait invisible dans les traces et mal employé par le modèle. |
| **Nomenclature comme outil** | Charger 300 codes dans le prompt système coûterait ~4 000 tokens à **chaque** tour, pour un besoin occasionnel. Un outil ne coûte que quand il sert. |

### 6.8 Pourquoi du texte français compact et non des `dict`

C'est un choix de **budget de tokens** et de **fiabilité**, pas d'esthétique.

Un seul événement rendu en JSON brut :

```json
{"event_id": "EVT-9912", "intercalaire_ref": "88412", "date": "2026-09-02T00:00:00",
 "update_date": "2026-09-03T00:00:00", "label": "Courrier (entrant ou sortant) — Arrivée courrier",
 "type_code": "1", "subtype_code": "1.1", "details": "reçu acte de notoriété…",
 "comment": "pièce classée…", "motif": null, "interlocuteur": "Notaire"}
```

≈ **150 tokens**, dont la moitié en clés répétées, guillemets, accolades et
`null`. La même information en une ligne :

```
02/09/2026 · EVT-9912 · Courrier (entrant ou sortant) — Arrivée courrier · Notaire · « reçu acte de notoriété… »
```

≈ **45 tokens**. Sur une page de 20 événements, l'écart est de **3 000 tokens
par appel** — et un dossier se parcourt en plusieurs pages.

Les trois autres raisons, aussi importantes :

1. **Les champs vides disparaissent.** `"motif": null` occupe des tokens et
   invite le modèle à commenter une absence. En texte, un motif absent
   n'existe simplement pas.
2. **Le format de citation est imposé.** La date française et la référence
   sont dans la ligne, donc dans le contexte : le modèle cite naturellement
   « le 02/09/2026 (EVT-9912) » (§ 9.3). Avec du JSON, il reformate, et
   reformater c'est l'occasion de se tromper.
3. **`raw` ne peut pas fuir.** Il n'existe plus dans les modèles (§ 2.3 b), et
   le rendu passe par `formatting.py` : il n'y a aucun chemin par lequel un
   payload XML ou une date de naissance brute atteindrait le modèle ou une
   trace (§ 10.5).

**Contrepartie assumée** : le texte n'est pas ré-analysable par machine. C'est
sans conséquence — le seul consommateur est un LLM, et les tests portent sur les
fonctions pures de `snapshot.py`, en amont du rendu (§ 12.1).

Tout le rendu est centralisé dans `formatting.py` (`format_event_line`,
`format_event_full`, `format_summary`, `format_amount`, …), ce qui en fait un
**contrat testé** plutôt qu'une habitude dispersée dans douze outils.

---

## 7 — Reconstruction du bloc-note (section critique)

> **Si une seule section de ce document doit être lue intégralement avant de
> coder, c'est celle-ci.** Les bloc-notes sont les commentaires libres des
> gestionnaires. Ce sont eux qui disent *pourquoi* un dossier a attendu six
> mois. Une chronologie de codes sans les bloc-notes ne permet pas de juger si
> un dossier a été bien géré. **Perdre du texte de bloc-note est inacceptable.**

### 7.1 Le mécanisme de troncature d'EDC

Les colonnes bloc-note d'EDC sont des `VARCHAR2` : `BLOCNOTEDOSSIER` au niveau
du dossier, `BLOCNOTEEVT` au niveau de l'événement. Quand la saisie dépasse la
capacité de la colonne — **autour de 256 caractères** — EDC coupe, et range
**la suite** dans le CLOB XML associé, sous le nœud :

```xml
<vo nom="racine">
  <string nom="blocNoteExtensible">…la suite du texte…</string>
  <string nom="commentaire">…un autre champ, distinct…</string>
</vo>
```

Deux conséquences décisives :

1. **La coupure tombe n'importe où**, y compris **au milieu d'un mot**. Ce
   n'est pas un découpage sémantique : c'est une troncature d'octets.
2. Le seuil n'est **pas exactement** 256. Selon que la colonne est déclarée en
   `CHAR` ou en `BYTE`, un texte contenant des accents (donc tous les textes
   français) est coupé plus tôt en nombre de caractères.

> **Règle absolue qui en découle : ne jamais décider de recoller en fonction de
> la longueur du texte.** Un `if len(note) >= 256:` serait un bug latent qui
> perdrait silencieusement du texte sur les notes accentuées. **La présence
> d'un `blocNoteExtensible` non vide est le seul critère.** Un test dédié
> verrouille ce point (§ 7.6, test 5).

C'est aussi la raison pour laquelle `bloc_note_dossier` et `detail_evenement`
ne tronquent **jamais** (§ 6.1) : il serait absurde de recoller un texte coupé
par la base pour le recouper nous-mêmes.

### 7.2 Le problème existe aux deux niveaux — et un seul est traité

| Niveau | Colonne | Suite dans | Traité dans `deces-risk` ? |
|---|---|---|---|
| Dossier | `ESDDOSSIER.BLOCNOTEDOSSIER` | `INFOSPECDOSSIER` → `blocNoteExtensible` | ✅ Oui, dans `QUERY_DOSSIER` |
| **Événement** | `ESDEVENEMENT.BLOCNOTEEVT` | `INFOSPECEVT` → `blocNoteExtensible` | ❌ **Non — c'est le trou à combler** |

Vérification faite sur le code source : ni
[`source.py::normalize_edc_event`](../../files_late_fee/src/deces_risk/datasources/edc/source.py),
ni `archive_scripts_eckert_law/eckert_prompt_generator.py::get_additional_note`
ne lisent `blocNoteExtensible` au niveau événement. Les deux se contentent de
`BLOCNOTEEVT` (donc tronqué) et y ajoutent le champ XML `commentaire`, qui est
**un autre champ** et non la suite du texte.

**Conséquence aujourd'hui** : sur tout événement dont la note dépasse ~256
caractères, la fin du commentaire du gestionnaire est perdue — et ce sont
précisément les notes longues qui portent l'explication.

### 7.3 Deux niveaux, deux implémentations — et pourquoi

| Niveau | Où se fait le recollage | Pourquoi |
|---|---|---|
| **Dossier** | **En SQL**, dans `QUERY_DOSSIER`, porté **tel quel** | La requête ne rapatrie pas `INFOSPECDOSSIER` par ailleurs. Faire le recollage côté Oracle (`XMLQuery` + `XMLCast` + `\|\|`) évite de transférer tout le CLOB sur le réseau pour n'en garder qu'un nœud. Le code existe, il est éprouvé, on n'y touche pas. |
| **Événement** | **En Python**, dans `normalize_edc_event` | `QUERY_EVENTS` sélectionne **déjà** `e.INFOSPECEVT`, parsé de toute façon pour en tirer `motif` et `commentaire`. Extraire un nœud de plus est gratuit. À l'inverse, le faire en SQL ajouterait un `XMLQuery` par ligne sur potentiellement 500 lignes, et déplacerait la logique hors de portée des tests unitaires. |

Cette asymétrie est **volontaire** et doit être commentée dans le code, sans
quoi un relecteur la prendra pour une incohérence. Le critère est simple : *le
recollage se fait là où le XML est déjà disponible sans coût supplémentaire.*

Le SQL du niveau dossier, porté sans modification :

```sql
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
```

Noter le `||` **sans séparateur** : le SQL fait déjà la bonne chose. C'est
exactement la règle du § 7.4, appliquée côté base.

### 7.4 La règle de recollage, et pourquoi `". "` est faux

**La règle, en une phrase :** la suite est **collée sans aucun séparateur** à la
base, parce que ce n'est pas un champ distinct mais **la continuation d'une
troncature** ; le champ XML `commentaire`, lui, est **un champ distinct** et
reste séparé.

```python
def stitch_bloc_note(base: str | None, overflow: str | None) -> str | None:
    """Reassemble a bloc-note truncated at the VARCHAR2 boundary.

    EDC stores the beginning of a long note in a VARCHAR2 column and the
    remainder in the ``blocNoteExtensible`` node of the associated XML CLOB.
    The cut can fall mid-word, so the two parts are concatenated with NO
    separator: anything inserted here would corrupt the word straddling the
    boundary.

    Args:
        base: Value of the VARCHAR2 bloc-note column (possibly truncated).
        overflow: Value of the ``blocNoteExtensible`` XML node.

    Returns:
        The reassembled note, or None when both parts are empty.
    """
    parts = [part for part in (base, overflow) if part and part.strip()]
    return "".join(parts) or None
```

**Pourquoi `". "` corrompt les données.** Le code actuel de `deces-risk`
assemble ainsi :

```python
details_parts = []
note = row.get("BLOCNOTEEVT")
if note and note != "Evénement Automatique":
    details_parts.append(str(note).replace("\n", " ").strip())
commentaire = get_xml_attribute(info_evt, "commentaire")
if commentaire:
    details_parts.append(commentaire.replace("\n", " ").strip())
return ". ".join(details_parts) or None  # ← le problème
```

Deux défauts **distincts**, à ne pas confondre :

**Défaut n° 1 — la perte pure et simple.** `blocNoteExtensible` n'est jamais
lu au niveau événement. Le texte au-delà de la troncature n'est pas « mal
assemblé » : il **n'arrive jamais** jusqu'au modèle.

**Défaut n° 2 — le piège du correctif naïf.** La correction évidente consiste
à ajouter `blocNoteExtensible` dans `details_parts`, où il hériterait du
`". ".join(...)`. Résultat sur une coupure en milieu de mot :

```
base     = "…dossier en attente, le notaire Me Dupont doit confir"
overflow = "mer la dévolution avant ordonnancement."

". ".join  → "…doit confir. mer la dévolution avant ordonnancement."   ❌
"".join    → "…doit confirmer la dévolution avant ordonnancement."     ✅
```

La version fautive fabrique une **fin de phrase là où il n'y en a pas**, coupe
un mot en deux fragments dont aucun n'est cherchable, et invente une ponctuation
que le gestionnaire n'a pas écrite. Un agent qui cite ce texte cite un document
qui n'existe pas. Le `.strip()` appliqué à chaque partie est tout aussi nocif
ici : il mangerait une espace significative à la jointure.

De plus, `". "` mélange dans un même champ deux choses de nature différente : la
note du gestionnaire et le `commentaire` XML. D'où la séparation :

```python
def normalize_edc_event(row: dict[str, Any]) -> Event:
    """Convert one QUERY_EVENTS row into a normalized Event."""
    info_evt = parse_info_spec(row.get("INFOSPECEVT"))
    info_inter = parse_info_spec(row.get("INFOSPECINTERCALAIRE"))

    base_note = row.get("BLOCNOTEEVT")
    overflow = get_xml_attribute(info_evt, "blocNoteExtensible")
    # The "automatic event" sentinel is only noise when it stands alone.
    if base_note == AUTOMATIC_EVENT_SENTINEL and not overflow:
        base_note = None
    details = stitch_bloc_note(base_note, overflow)

    comment = get_xml_attribute(info_evt, "commentaire")
    ...
    return Event(..., details=details, comment=comment, ...)
```

| Champ du modèle | Contenu | Assemblage |
|---|---|---|
| `Event.details` | la note du gestionnaire, **reconstituée entière** | `base + overflow`, **sans séparateur** |
| `Event.comment` | le nœud XML `commentaire`, champ distinct | **pas concaténé** au détail |

Le `commentaire` est ensuite **ajouté avec son propre séparateur au moment du
rendu**, dans `formatting.py` — un saut de ligne et une étiquette, jamais un
`". "` :

```
Bloc-note :
reçu acte de notoriété du notaire Me Dupont, transmis au service succession…
Commentaire (XML) :
pièce classée au dossier le 03/09.
```

Séparer dans le modèle et n'assembler qu'au rendu donne trois avantages : la
recherche plein texte (§ 6.4) peut cibler l'un ou l'autre, le rendu reste
modifiable sans retoucher la normalisation, et surtout **il n'existe plus aucun
chemin de code où un séparateur pourrait s'insérer dans une troncature**.

### 7.5 Le nettoyage des retours à la ligne

`deces-risk` applique `.replace("\n", " ")` à la note. **Ce nettoyage est
supprimé.** Les retours à la ligne d'un bloc-note sont de l'information : ils
séparent des interventions successives, souvent datées à la main par le
gestionnaire. Les écraser rend le texte illisible pour le modèle comme pour
l'humain qui vérifie une citation. Le format « une ligne » n'est nécessaire que
dans les **listes**, où il est obtenu par la troncature de rendu (§ 6.1) — donc
dans `formatting.py`, et non dans les données.

### 7.6 Les tests qui verrouillent ce comportement

Fichier dédié : `tests/test_bloc_note.py`. Ces tests sont **des fonctions
pures**, sans Oracle. Ils sont la garantie que le § 7 ne régressera pas.

| # | Test | Entrée | Attendu |
|---|---|---|---|
| 1 | `test_long_note_with_overflow` | base de 256 car. finissant par `"…doit confir"`, overflow `"mer la dévolution."` | `details` contient `"doit confirmer la dévolution."` ; **aucun** `". "` inséré ; `len(details) == len(base) + len(overflow)` |
| 2 | `test_long_note_without_overflow` | base longue, `INFOSPECEVT` sans `blocNoteExtensible` | `details == base`, **caractère pour caractère** (ni `strip`, ni `replace`) |
| 3 | `test_overflow_without_base` | `BLOCNOTEEVT` à `None`, overflow présent | `details == overflow`, sans espace ni ponctuation de tête |
| 4 | `test_neither_present` | ni base ni overflow (et `""`, `"   "`) | `details is None` — pas `""` |
| 5 | `test_no_length_heuristic` | base **courte** (20 car.) + overflow présent | les deux sont concaténés quand même ; verrouille la règle du § 7.1 |
| 6 | `test_comment_stays_separate` | note + `commentaire` XML | `details` == la note seule ; `event.comment` == le commentaire ; le rendu les met sur **deux lignes étiquetées** ; `". "` n'apparaît nulle part |
| 7 | `test_automatic_event_sentinel` | `BLOCNOTEEVT == "Evénement Automatique"` | sans overflow → `details is None` ; **avec** overflow → les deux sont conservés (le sentinelle n'écrase pas du texte réel) |
| 8 | `test_newlines_are_preserved` | note contenant `"\n"` | `"\n"` présent dans `details` ; la ligne de liste, elle, est bien sur une seule ligne |
| 9 | `test_dossier_note_from_sql` | ligne simulée où `BLOC_NOTE` contient déjà la concaténation faite par Oracle | `dossier.global_comment` la reprend telle quelle ; `None` et `""` → `None` |
| 10 | `test_real_xml_payload` | un `INFOSPECEVT` complet en fixture (`tests/fixtures/infospec_evt.xml`), avec `blocNoteExtensible` **et** `commentaire` | extraction correcte des deux nœuds par `get_xml_attribute` ; non-régression sur la recherche récursive |
| 11 | `test_full_note_reaches_the_tool` | événement long dans la fixture de `CaseFile` | `detail_evenement` renvoie le texte **entier** ; `lister_evenements` le tronque à 160 car. **et** l'indique |

Le test 1 mérite son assertion sur les longueurs : c'est la seule qui détecte
mécaniquement l'ajout d'un séparateur, quel qu'il soit.

Et un test vivant côté § 12.4 (`-m live`) : sur un dossier réel connu pour avoir
une note longue, vérifier que `len(details) > 256` — c'est la seule preuve que
la chaîne complète (SQL → handler LOB → XML → recollage) fonctionne en vrai.

### 7.7 Le handler LOB, condition nécessaire

Tout ce qui précède suppose que `INFOSPECEVT` arrive en `str`. C'est le rôle de
`_lob_output_handler`, porté tel quel : sans lui, `oracledb` renvoie des objets
LOB, et `get_xml_attribute` reçoit autre chose que du texte. Le handler traite
aussi les `VARCHAR2` de plus de 256 caractères — ce qui, précisément, est le cas
des colonnes bloc-note. **Il n'est pas optionnel.**

`parse_info_spec` conserve malgré tout la branche `value.read()` pour le cas où
le handler ne serait pas posé sur un curseur : c'est une ceinture de sécurité à
coût nul, déjà présente dans le code source.

---

## 8 — Le backend LLM

### 8.1 Une seule fabrique, pilotée par l'environnement

```python
@cache
def get_chat_model() -> BaseChatModel:
    """Return the process-wide chat model built from the environment."""
    return build_chat_model(VLMConfig())


def build_chat_model(config: VLMConfig, *, temperature: float | None = None) -> ChatOpenAI:
    """Build a LangChain chat model backed by the OpenAI-compatible vLLM server.

    Args:
        config: VLM settings (base URL, API key, model name, generation).
        temperature: Override for ``config.temperature``.

    Returns:
        ChatOpenAI pointed at VLM_BASE_URL.

    Raises:
        ValueError: If VLM_BASE_URL or VLM_MODEL is missing.
    """
```

Fonction portée depuis
[`llm/client.py`](../../files_late_fee/src/deces_risk/llm/client.py), à laquelle
s'ajoute `extra_body` : les paramètres de la section `vlm` de `config.yaml`
(raisonnement `enable_thinking` / `reasoning_effort` fusionnés dans
`chat_template_kwargs`, et tout paramètre vLLM libre : `top_p`, `top_k`…).
C'est le
**seul** endroit du projet qui instancie un modèle : changer de backend, c'est
changer ces quelques lignes, sans toucher au graphe, aux outils ni aux prompts.

> **Pas de couche d'abstraction supplémentaire.** Pas de registre de
> fournisseurs, pas de `Protocol` : l'interface `BaseChatModel` de LangChain
> *est* déjà l'abstraction. En ajouter une seconde serait de la configurabilité
> non demandée.

Modèle cible : **Qwen/Qwen3.8-27B**, avec raisonnement `medium` et
l'échantillonnage officiel du mode raisonnement (température 1,0, `top_p` 0,95,
`top_k` 20). Qwen déconseille les températures basses avec raisonnement
(répétitions en boucle) ; la fidélité des citations repose sur la lecture des
outils (§ 9.3), pas sur la température. Serveur :

```bash
vllm serve Qwen/Qwen3.8-27B --max-model-len 262144 --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml
```

`--reasoning-parser qwen3` n'est pas optionnel : sans lui, la réflexion arrive
dans `content`, s'affiche et reste dans l'historique. `ChatOpenAI` n'extrait
pas `reasoning_content` : la réflexion n'est jamais renvoyée au modèle
(`preserve_thinking` est donc sans objet), ce qui préserve le contexte.

### 8.2 Prérequis dur : le *tool calling* natif

**L'agent ne fonctionne pas sans appel d'outils natif.** Toute la conception
repose sur `bind_tools` + `tools_condition` : si le modèle servi ne sait pas
émettre de `tool_calls` structurés, il n'y a pas d'agent, seulement un modèle
qui écrit du texte sur un dossier qu'il n'a pas lu.

Ce prérequis porte sur **trois** capacités, à vérifier séparément :

| Capacité | Pourquoi elle est nécessaire |
|---|---|
| Émettre un `tool_call` avec des arguments JSON valides | sinon aucun outil ne s'exécute |
| Enchaîner **plusieurs tours** d'outils | l'agent appelle typiquement 3 à 6 outils avant de répondre |
| Accepter un historique contenant des `ToolMessage` | sinon la boucle casse au second tour |

Le troisième point est celui qui échoue le plus souvent en pratique sur un
modèle mal servi, et c'est aussi le moins testé par les tutoriels.

### 8.3 Vérifier avant de construire (à faire en premier)

Un script de 20 lignes, à lancer **avant** d'écrire la moindre ligne d'agent.
Il est rangé dans `scripts/00_check_tool_calling.py` et fait partie de la
définition de « prêt à démarrer ».

```bash
# 1. Le serveur répond et le modèle porte bien le nom attendu
curl -s "$VLM_BASE_URL/models" | jq '.data[].id'
```

```python
# 2. Aller-retour complet d'appel d'outil
from langchain_core.tools import tool


@tool("additionner", parse_docstring=True)
def add(a: int, b: int) -> int:
    """Additionne deux entiers.

    Args:
        a: Premier entier.
        b: Second entier.
    """
    return a + b


model = build_chat_model(VLMConfig()).bind_tools([add])

first = model.invoke([HumanMessage("Combien font 17 plus 25 ?")])
assert first.tool_calls, "PAS DE TOOL CALLING — voir le repli § 8.4"
assert first.tool_calls[0]["name"] == "additionner"
assert first.tool_calls[0]["args"] == {"a": 17, "b": 25}

# Le tour suivant : le modèle doit savoir lire un ToolMessage.
second = model.invoke(
    [
        HumanMessage("Combien font 17 plus 25 ?"),
        first,
        ToolMessage("42", tool_call_id=first.tool_calls[0]["id"]),
    ]
)
assert not second.tool_calls and "42" in second.content
```

À vérifier aussi, dans la foulée, parce que ce sont les trois pièges du terrain :

1. **Les noms français passent.** Le nom d'outil doit revenir exactement
   `"additionner"`, non translittéré. Tester également un nom accentué du
   catalogue (`"rafraichir_dossier"` est volontairement sans accent, mais
   `"statistiques_chronologie"` est long : vérifier qu'il n'est pas tronqué).
2. **Les arguments optionnels.** Un outil à six paramètres optionnels
   (comme `lister_evenements`) est un cas nettement plus dur qu'`additionner` :
   refaire le test avec la vraie signature et vérifier que le modèle n'invente
   pas de paramètre et n'envoie pas `"null"` en chaîne.
3. **Le serveur vLLM est bien lancé avec le bon analyseur.** L'appel d'outils
   n'est actif que si vLLM tourne avec `--enable-auto-tool-choice` et le
   `--tool-call-parser` correspondant à la famille du modèle. Un modèle
   parfaitement capable renverra `tool_calls = []` si le serveur n'est pas
   configuré ainsi — **c'est la cause n° 1 d'un échec au test ci-dessus**, et
   elle se corrige côté serveur, pas côté code.

### 8.4 Protocole de repli : invocation JSON contrainte

Si, après correction de la configuration serveur, les `tool_calls` restent peu
fiables (arguments mal formés, outil inexistant, boucle sur le même appel), on
bascule sur un **repli explicite**, sans toucher au reste de l'architecture.

**Principe.** Le modèle n'émet plus de `tool_calls` : il émet un objet JSON
unique, contraint par une grammaire côté serveur, que le nœud `agent` traduit
lui-même en appel d'outil.

```python
class ToolInvocation(BaseModel):
    """Fallback protocol: one tool call expressed as constrained JSON."""

    outil: Literal["charger_dossier", "lister_evenements", ...]  # généré depuis ALL_TOOLS
    arguments: dict[str, str | int | None] = Field(default_factory=dict)
    reponse_finale: str | None = None  # renseigné quand aucun outil n'est nécessaire
```

**Mise en œuvre.**

1. Le catalogue d'outils (noms, descriptions françaises, arguments) est rendu
   **dans le prompt système** au lieu d'être passé à `bind_tools`. Il est
   généré depuis `ALL_TOOLS`, donc jamais désynchronisé.
2. Le modèle est appelé avec le `response_format` standard OpenAI
   (`{"type": "json_schema", "json_schema": {"name": "ToolInvocation", "schema": …}}`),
   que vLLM applique au décodage — il **garantit** la forme plutôt que de
   l'espérer, et s'applique après la réflexion avec `--reasoning-parser`.
   (`guided_json`, déprécié puis retiré de vLLM, n'est plus utilisé.)
3. Le nœud `agent` convertit le résultat en `AIMessage(tool_calls=[…])` — donc
   **`ToolNode` et le graphe ne changent pas d'une ligne**.

**Ce que le repli coûte** : un seul outil par tour (plus d'appels parallèles),
quelques centaines de tokens de catalogue à chaque tour, et une trace Langfuse
un peu moins lisible. **Ce qu'il ne coûte pas** : aucun changement dans l'état,
les outils, les tests, ni le prompt métier.

Le basculement est piloté par `vlm.tool_protocol: native|json` dans
`config.yaml` (défaut `native`, surchargeable par `VLM_TOOL_PROTOCOL`), lu dans
`agent_node`. C'est la **seule** option de
configurabilité « au cas où » de ce projet, et elle est justifiée par un risque
identifié au § 14.

---

## 9 — Prompt système

Le prompt vit dans [`prompts.py`](../src/agent_edc/prompts.py), en une constante
`SYSTEM_PROMPT`. Pas de moteur de templates : il n'y a qu'un prompt, et il ne
dépend d'aucune variable (l'état du dossier arrive par les outils, jamais par le
prompt). L'isoler dans son propre module permet à un métier de le relire sans
lire de Python.

### 9.1 Rôle

> Tu es un assistant qui aide un **gestionnaire de dossiers décès** de CNP
> Assurances à examiner **un seul dossier E-décès** à la fois. Tu travailles
> exclusivement à partir des données E-décès que tes outils te fournissent. Tu
> es en **lecture seule** : tu ne modifies rien, tu ne promets aucune action.
> Tu réponds **toujours en français**, de façon factuelle et concise.
> Ton interlocuteur est un professionnel : pas de pédagogie inutile, pas de
> formules de politesse à rallonge.

### 9.2 Ce que « bonne gestion » veut dire

Le prompt énonce les points de contrôle, en précisant qu'ils sont des **pistes
d'examen** et non un barème (le barème, c'est la phase 2, § 13) :

> Pour juger si un dossier a été correctement traité, examine :
>
> 1. **La continuité.** Y a-t-il de longues périodes sans le moindre acte ?
>    Un intervalle de plusieurs mois n'est acceptable que s'il est **motivé**
>    par un événement de mise en attente (attente d'un notaire, d'un
>    généalogiste, d'une décision judiciaire).
> 2. **Les relances.** Quand des pièces ont été demandées, ont-elles été
>    relancées ? À quel rythme ? Les relances se sont-elles arrêtées sans
>    explication ?
> 3. **Les bénéficiaires.** Sont-ils tous identifiés ? Reste-t-il des
>    bénéficiaires **présumés** jamais qualifiés ? Certains ont-ils un restant
>    à payer alors que le dossier paraît terminé ?
> 4. **Les montants.** Un restant à payer non nul sur un dossier ancien est le
>    signal le plus fort : c'est l'assiette d'éventuels intérêts de retard.
> 5. **Les alertes.** Les événements de type « Alerte » ont-ils été suivis d'un
>    acte, ou laissés sans suite ?
> 6. **Les mises en attente et la clôture.** Ont-elles un motif renseigné ? Le
>    motif est-il cohérent avec ce que disent les bloc-notes ?
> 7. **Les bloc-notes.** Ils expliquent souvent ce que la chronologie ne montre
>    pas. Une décision non tracée dans un bloc-note est une décision
>    invérifiable.
>
> Ces points sont des pistes d'examen, pas une grille de notation. **Tu ne
> délivres pas de note ni de verdict binaire** : tu décris ce que tu observes,
> tu signales ce qui te paraît anormal, et tu dis explicitement ce que les
> données ne permettent pas de trancher.

### 9.3 Comment citer

> Chaque affirmation factuelle doit être **traçable**. Tu cites :
>
> - **la date au format JJ/MM/AAAA** ;
> - **la référence de l'événement** telle qu'elle t'a été donnée, entre
>   parenthèses : « le 14/07/2024 (EVT-4102) » ;
> - **un extrait littéral** de bloc-note quand il porte l'information, entre
>   guillemets français, **sans le reformuler** : « … le notaire n'a pas
>   répondu à la relance du 12/06 … ».
>
> Un extrait de bloc-note se cite **tel quel**. Si tu dois le raccourcir,
> utilise « … » ; ne corrige ni l'orthographe, ni la ponctuation, ni les
> abréviations du gestionnaire.
>
> Tu ne cites jamais une date, une référence ou un extrait que tes outils ne
> t'ont pas fournis dans cette conversation.

### 9.4 Quand appeler un outil plutôt que deviner

> - **Dès qu'un identifiant à 8 chiffres apparaît**, appelle `charger_dossier`.
>   Appelle-le **seul**, attends son résultat, puis enchaîne : les autres outils
>   ne voient le dossier qu'une fois le chargement terminé.
> - **Ne réponds jamais de mémoire sur le contenu d'un dossier.** Si une
>   information ne figure pas dans ce que tes outils t'ont renvoyé, appelle
>   l'outil qui la contient. Si aucun outil ne la contient, dis-le.
> - Avant de te prononcer sur la qualité de la gestion, lis **au minimum**
>   `bloc_note_dossier` et `statistiques_chronologie` : sans eux, tu juges à
>   l'aveugle.
> - Pour citer un événement, appelle `detail_evenement` : les listes sont
>   **tronquées** et citer une troncature, c'est citer un texte incomplet.
> - Appelle `rafraichir_dossier` si le gestionnaire vient de saisir quelque
>   chose, si la question porte sur la situation d'aujourd'hui, ou si
>   l'instantané a plus d'une demi-heure et que la question porte sur l'état
>   courant. Sinon, ne le fais pas : les données en mémoire suffisent.
> - **Tu n'as accès qu'à E-décès.** Tu ne vois ni les documents (GED), ni
>   Numéa, ni Yvoire, ni l'identité du défunt, ni les contrats. Si la question
>   en dépend, dis clairement que cette information est hors de ta portée.

### 9.5 Ne jamais inventer un libellé de nomenclature

> Les codes E-décès (type et sous-type d'événement, motif, nature
> d'interlocuteur) ont des libellés **officiels**. Tu ne les devines jamais,
> même si le code te paraît transparent.
>
> - Si un libellé t'a été donné par un outil, reprends-le **mot pour mot**.
> - Si tu n'as qu'un code brut, appelle `libelle_nomenclature`.
> - Si `libelle_nomenclature` ne connaît pas le code, écris exactement :
>   « le code X n'est pas répertorié dans les nomenclatures dont je dispose ».
>   **Ne propose pas d'interprétation.**
> - Un libellé affiché comme « sous-événement inconnu » signifie que le code
>   est absent des tables : signale-le comme tel, ce n'est pas une erreur de ta
>   part.

### 9.6 Ton, incertitude et langue

> - Réponds **en français**, y compris si la question est posée dans une autre
>   langue, et y compris pour reprendre un terme technique.
> - Sois bref : quelques phrases, ou une liste à puces. Le gestionnaire connaît
>   le métier.
> - Quand les données ne permettent pas de conclure, écris-le : « les
>   événements ne disent pas pourquoi le dossier est resté sans acte entre
>   juillet 2024 et février 2025 ». **Une absence d'information est une
>   réponse acceptable ; une information inventée ne l'est pas.**
> - N'affirme jamais qu'une action a été faite parce qu'elle « aurait dû »
>   l'être.

### 9.7 Taille et maintenance

Le prompt complet tient en **~700 tokens**. Il est envoyé à chaque tour
(§ 4.2) : c'est le coût fixe de la conversation, à mettre en regard des ~15 000
tokens qu'aurait coûté l'injection du dossier entier.

Il ne contient **aucune nomenclature** (§ 6.7) et **aucune donnée de dossier** :
il reste donc valable pour tous les dossiers, et modifiable sans invalider les
fils déjà persistés.

---

## 10 — Observabilité Langfuse

### 10.1 Deux besoins distincts

| Besoin | Outil | Destinataire |
|---|---|---|
| Déboguer une exécution (SQL lent, LOB tronqué, exception) | **loguru** | le développeur, en local |
| Comprendre *pourquoi le modèle a répondu ça* (enchaînement d'outils, prompts, tokens) | **Langfuse** | le développeur et le métier |

Les deux sont indépendants : Langfuse peut être coupé sans rien perdre du
débogage technique.

`setup_logging(level, log_dir)` est porté tel quel : une sortie console au
niveau choisi, un fichier tournant en `DEBUG`.

### 10.2 Le câblage, qui ne casse jamais

```python
def build_langfuse_callbacks(config: LangfuseConfig) -> list[Any]:
    """Build the Langfuse LangChain callbacks, or an empty list.

    Returns an empty list — never raises — when tracing is disabled, when the
    keys are missing, or when the SDK fails to initialize.
    """
```

Fonction portée telle quelle. Les trois propriétés à préserver mot pour mot :

1. `langfuse.enabled: false` (le **défaut**) ⇒ liste vide, aucun import du SDK ;
2. clés absentes ⇒ liste vide + un `DEBUG` dans les journaux ;
3. échec d'initialisation ⇒ `except Exception` large, `WARNING`, liste vide.

> **La règle :** le traçage est un confort. Un serveur Langfuse injoignable ne
> doit **jamais** empêcher un gestionnaire d'obtenir sa réponse. Le `except
> Exception` large est ici justifié, contrairement à l'usage général.

Les rappels sont attachés une fois, à la compilation :

```python
callbacks = build_langfuse_callbacks(get_settings().langfuse, tags=trace_tags())
graph = graph.with_config({"callbacks": callbacks}) if callbacks else graph
```

### 10.3 Session et tags : fil → session, dossier → tag

Une conversation = un fil LangGraph = **une session Langfuse**. C'est ce qui
permet de relire les 12 tours d'un échange dans l'ordre plutôt que 12 traces
orphelines.

Le `CallbackHandler` Langfuse ne lit la session et les tags **que** dans les
métadonnées du run racine : les poser sur un appel de modèle, à l'intérieur
d'un nœud, reste sans effet. `observability.py` sous-classe donc le handler
(`EdcCallbackHandler`) :

- au démarrage du run racine, `metadata["thread_id"]` — que LangGraph recopie
  depuis `configurable`, sous `langgraph dev` comme dans le REPL — devient
  `langfuse_session_id`, et les tags de configuration sont ajoutés ;
- à la fin du run racine, l'état final contient `edc_id` : le tag
  `dossier:…` est posé sur le span racine (API interne du SDK, protégée par
  un `try/except`).

| Attribut Langfuse | Valeur | Usage |
|---|---|---|
| `session_id` | le `thread_id` LangGraph | rejouer une conversation entière |
| `tags` | `dossier:01234567` | retrouver tout ce qui a été dit sur un dossier |
| `tags` | `modele:…`, `raisonnement:off/low/medium/xhigh` | comparer les réglages de `config.yaml` |
| `tags` (ajoutés) | `repli:json`, `compactage:on` | comparer les protocoles (§ 8.4) et le compactage (§ 4.6) |

Le REPL termine par `flush_tracing()` : un processus court ne doit pas perdre
les derniers événements en attente d'envoi.

### 10.4 Nommer les spans et choisir les métadonnées

**Noms.** Les nœuds s'appellent `agent` et `tools` ; chaque outil apparaît sous
son **nom français**, ce qui rend la trace lisible sans décodeur :

```
session 4f2a… · tags [dossier:01234567]
└─ agent                          1,2 s   842 → 61 tokens
   └─ tools
      └─ charger_dossier          3,4 s   {cache_hit: false, rows_fetched: 171, events: 137}
   └─ agent                       2,1 s  1 704 → 48 tokens
   └─ tools
      └─ statistiques_chronologie 0,001 s {snapshot_age_s: 12, result_chars: 412}
      └─ bloc_note_dossier        0,001 s {snapshot_age_s: 12, result_chars: 1842}
   └─ agent                       3,8 s  3 190 → 402 tokens
```

**Métadonnées utiles**, posées par un petit décorateur qui remplace
`instrumented_node` :

```python
def traced_tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Log duration and attach tool metadata to the tool's Langfuse span.

    A no-op on the Langfuse side when tracing is disabled; the loguru log is
    always emitted.
    """
```

Le span d'un outil n'est pas le span « courant » du SDK : `update_current_span`
écrirait ailleurs. Le wrapper déclare donc un paramètre `callbacks`, que
LangChain remplit avec le gestionnaire enfant de l'outil (et masque du schéma
envoyé au modèle) ; son `parent_run_id` identifie le run de l'outil, et
`EdcCallbackHandler.attach_tool_metadata` met à jour le span correspondant.

| Métadonnée | Posée par | Ce qu'elle permet de diagnostiquer |
|---|---|---|
| `rows_fetched` | `charger_dossier`, `rafraichir_dossier` | volume ramené d'Oracle ; un dossier à 3 000 lignes explique une lenteur |
| `events`, `beneficiaries` | idem | taille de l'instantané, à corréler avec le § 14 (gros dossiers) |
| `load_duration_ms` | idem | isoler Oracle du reste |
| `cache_hit` | `charger_dossier` | détecter un modèle qui recharge en boucle — **le symptôme le plus coûteux** |
| `snapshot_age_s` | tous les outils de lecture | répondre à « la réponse était-elle fraîche ? » a posteriori |
| `result_chars` | tous | repérer les sorties qui gonflent le contexte |
| `truncated` | listes et recherche | savoir si le modèle a cité sur une base tronquée |
| `match_count` | `chercher_evenements` | distinguer « rien trouvé » de « mal cherché » |

Pas de métadonnée « pour voir » : chacune répond à une question de diagnostic
précise.

### 10.5 Ce qui ne doit jamais entrer dans une trace

**Règle 1 — aucun identifiant technique, jamais.** Ni mot de passe, ni DSN, ni
nom d'hôte, ni chemin de wallet, ni clé d'API. Ce n'est pas qu'une consigne :
c'est **structurel**. `EDCConfig` et `VLMConfig` ne transitent ni par l'état, ni
par les arguments d'outils, ni par leurs retours. Les outils lisent leur
configuration depuis l'environnement, à l'intérieur de leur corps. Aucun objet
de configuration n'est donc sérialisable dans une trace.
Corollaire : un message d'erreur Oracle (qui contient le DSN et parfois
l'utilisateur) **ne remonte jamais au modèle** : il part dans loguru, et l'outil
renvoie la phrase générique du § 6.1.

**Règle 2 — pas de données brutes de personnes.** Un dossier décès contient des
noms et des dates de naissance réels. Deux garde-fous :

- `raw` n'existe plus dans les modèles (§ 2.3 b) : les payloads XML et les
  colonnes d'identité brutes ne peuvent pas atteindre une trace ;
- une **fonction de masquage** est passée au client Langfuse, qui s'applique à
  tout ce qui est envoyé :

  ```python
  Langfuse(public_key=…, secret_key=…, host=…, mask=mask_trace_payload)
  ```

  `mask_trace_payload` remplace les dates de naissance (`JJ/MM/AAAA` précédé de
  « né(e) le ») par `[date masquée]`, et neutralise tout motif ressemblant à un
  secret (`password=`, `api_key=`, `dsn=`).

**Règle 3 — honnêteté sur ce qui reste.** Le masquage ne peut pas supprimer les
noms des bénéficiaires : ils sont dans les questions du gestionnaire et dans les
réponses de l'agent, qui *sont* la trace. Conclusion opérationnelle, à écrire
noir sur blanc :

> **Le traçage Langfuse n'est activable que sur une instance interne.**
> `langfuse.enabled: false` est le défaut ; l'activer contre une instance
> externe ou SaaS reviendrait à exporter des données personnelles hors de
> l'environnement, et n'est pas autorisé par ce projet.

**Règle 4 — les journaux suivent la même discipline.** loguru trace des
**compteurs** (`"EDC: {} event(s) for dossier {}"`), pas des contenus. Aucun
bloc-note, aucun nom n'est journalisé, même en `DEBUG`.

---

## 11 — Service et persistance

### 11.1 `langgraph.json`

```json
{
  "dependencies": ["."],
  "graphs": {
    "agent_edc": "./src/agent_edc/agent/build.py:graph"
  },
  "env": ".env",
  "python_version": "3.11"
}
```

```bash
uv run langgraph dev          # http://127.0.0.1:2024 + LangGraph Studio
```

`langgraph dev` recharge à chaud : modifier un outil ou le prompt ne demande pas
de redémarrage. `langgraph-cli[inmem]` est une dépendance **de développement**
uniquement.

### 11.2 Persistance : deux chemins, et il faut le savoir

C'est le point où l'on perd une demi-journée si on ne l'a pas lu.

| Exécution | Qui fournit la persistance | Les fils survivent-ils à un redémarrage ? |
|---|---|---|
| `langgraph dev` (serveur) | **le serveur**, dans `.langgraph_api/` | **Oui** — le serveur persiste localement entre deux lancements |
| REPL Typer / tests (§ 11.4) | **nous**, via `SqliteSaver` | **Oui** — fichier SQLite |

> **Le point à retenir :** un `checkpointer` passé à `compile()` est **ignoré**
> quand le graphe tourne sous le serveur LangGraph, qui impose le sien. D'où la
> forme de `build_agent(checkpointer=None)` (§ 4.1) : l'objet `graph` exporté
> vers `langgraph.json` est compilé **sans** checkpointer, et seul le REPL en
> fournit un.

```python
# cli.py
with SqliteSaver.from_conn_string(settings.agent.checkpoint_db) as checkpointer:
    agent = build_agent(checkpointer=checkpointer)
    agent.invoke(
        {"messages": [HumanMessage(question)]},
        config={"configurable": {"thread_id": thread_id}},
    )
```

Dans les deux cas, ce qui est persisté est **l'état complet**, donc
`messages` **et** `snapshot` : rouvrir une conversation de la veille retrouve le
dossier en mémoire sans requête Oracle (§ 5.6). L'âge affiché sera en revanche
de plusieurs heures, ce qui déclenchera la mention de fraîcheur du § 5.5 — le
comportement voulu.

`SqliteSaver` sérialise les modèles pydantic sans configuration particulière.
Un point à vérifier au premier essai : la reprise d'un fil ancien après une
**modification du modèle** `CaseFile` (champ ajouté). Mitigation simple et
suffisante : tout champ ajouté aux modèles porte une valeur par défaut, de sorte
qu'un ancien instantané se relise toujours.

### 11.3 Agent Chat UI

L'interface est [Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui),
lancée à côté du serveur. Rien à écrire : elle parle le protocole LangGraph.

| Variable (côté interface) | Valeur locale | Rôle |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:2024` | l'adresse du serveur `langgraph dev` |
| `NEXT_PUBLIC_ASSISTANT_ID` | `agent_edc` | la clé déclarée dans `langgraph.json` |
| `LANGGRAPH_API_URL` | *(mode proxy uniquement)* | lorsqu'on passe par la route serveur de l'interface |
| `LANGSMITH_API_KEY` | *(mode proxy uniquement)* | non utilisé ici — on ne trace pas vers LangSmith |

En local, les deux premières suffisent. L'interface gère le `thread_id` : c'est
lui qui devient la session Langfuse (§ 10.3) et la clé du checkpoint.

Ce que l'interface apporte gratuitement : l'affichage des appels d'outils et de
leurs résultats. Voir en clair que l'agent a appelé `bloc_note_dossier` puis
`detail_evenement("EVT-9912")` est **le principal outil de confiance** pour un
gestionnaire — il vérifie la citation d'un coup d'œil.

### 11.4 Le REPL Typer

Pour déboguer sans serveur ni interface web (et pour tester Langfuse sans
dépendre de l'hôte, § 10.3) :

```bash
uv run agent-edc chat 01234567          # ouvre une session sur un dossier
uv run agent-edc chat --thread abc123   # reprend une conversation existante
uv run agent-edc ask 01234567 "ce dossier est-il bien géré ?"   # un seul tour
uv run agent-edc check                  # vérifie EDC + vLLM + tool calling (§ 8.3)
```

Périmètre volontairement minimal : une boucle `while` qui lit une ligne,
`stream()` la réponse, et affiche les appels d'outils en gris via `rich`. Le
`thread_id` est un UUID affiché au démarrage pour pouvoir reprendre la session.
`chat 01234567` injecte simplement un premier message contenant l'identifiant —
aucun chemin de code spécial, donc le REPL exerce exactement le même graphe que
l'interface web. L'aide de la CLI est **en français** (c'est de l'interface
utilisateur), le code reste en anglais.

---

## 12 — Stratégie de tests

**Objectif :** `uv run pytest` passe en quelques secondes **sans Oracle, sans
vLLM, sans réseau**. C'est la propriété qui rend le projet développable hors de
l'environnement sécurisé — et c'est déjà celle de `deces-risk` (cf.
[09 — Tests](../../files_late_fee/docs/09-tests.md)).

```bash
uv run pytest                 # la suite par défaut : tout sauf live et parity
uv run pytest -m parity       # comparaison des nomenclatures (§ 2.4)
uv run pytest -m live         # nécessite Oracle + wallet + vLLM (§ 12.4)
```

Configuration : `addopts = "-m 'not live and not parity'"` dans
`[tool.pytest.ini_options]`.

### 12.1 Ce qui est testable sans rien (l'essentiel)

L'architecture est faite pour que **`loader.py` soit le seul module impur**.
Tout le reste est une fonction pure.

| Cible | Fichier | Ce qui est vérifié |
|---|---|---|
| Identifiants | `test_normalizers.py` | `normalize_edc_id` : 7→8 chiffres, tirets et espaces, non-chiffres rejetés |
| Libellés | `test_normalizers.py` | clé composée `"1.1"`, alertes via `SOUS_TYPE_ALERTE`, code inconnu → « sous-événement inconnu » |
| Motifs | `test_normalizers.py` | `MOTIF_FT` par défaut, `MOTIF_ATTENTE` sur sous-type `M8`, code inconnu renvoyé tel quel |
| XML | `test_normalizers.py` | `get_xml_attribute` en récursif, XML illisible → `None` sans lever |
| Montants | `test_normalizers.py` | centimes → euros, agrégation par intercalaire, valeurs vides ignorées |
| Bénéficiaires | `test_normalizers.py` | fusion identité + montants, dédoublonnage par intercalaire, présumé = type `'6'`, identité absente |
| **Bloc-note** | **`test_bloc_note.py`** | **les 11 tests du § 7.6** |
| Filtres et tri | `test_snapshot.py` | dates incluses aux bornes, filtre par type (code **et** libellé), filtre par bénéficiaire, ordre récent/ancien, événements sans date en dernier |
| Pagination | `test_snapshot.py` | première page, page du milieu, dernière page, décalage hors bornes, `limite` bornée à 50, `limite=0` → 1, décalage négatif → 0 |
| Recherche | `test_snapshot.py` | insensible casse et accents, porte sur les 4 champs, extrait centré sur la correspondance, terme absent → 0 |
| Statistiques | `test_snapshot.py` | plus long intervalle et ses deux bornes, moyenne, répartition par type, dossier à 0 et à 1 événement |
| Rendu | `test_formatting.py` | format de date français, montants `42 300,00 €`, champs vides omis, troncature à 160 car. avec `…`, **absence de troncature** dans le rendu complet |

Les cas limites cités (dossier à 0 événement, à 1 événement, bénéficiaire sans
montant) ne sont pas décoratifs : ce sont les divisions par zéro et les
`max()` sur liste vide qui font planter un agent en démonstration.

### 12.2 Les outils, sur une fixture d'instantané

Une fixture unique dans `conftest.py`, taillée pour exercer les cas réels :

```python
@pytest.fixture
def case_file() -> CaseFile:
    """A realistic in-memory snapshot: 40 events over 2 years, 3 beneficiaries."""
```

| Élément de la fixture | Ce qu'il permet de tester |
|---|---|
| 40 événements sur 2 ans | pagination réelle (2 pages) |
| un **trou de 7 mois** | `statistiques_chronologie` |
| un événement à note longue **avec** débordement | § 7.6 test 11 |
| un événement à note longue **sans** débordement | non-régression |
| un événement « Evénement Automatique » | filtrage du sentinelle |
| un événement sans date | tri, et statistiques |
| une alerte, un courrier, une communication | filtre par type |
| 3 bénéficiaires : soldé / non soldé / présumé sans montant | `synthese_montants`, § 6.5 |
| deux bénéficiaires de même nom de famille | désambiguïsation de `detail_beneficiaire` |
| un bloc-note dossier de 1 800 caractères | `bloc_note_dossier` non tronqué |

**Comment on appelle un outil dans un test.** Les arguments injectés
(`InjectedState`, `InjectedToolCallId`) ne sont pas fournis par le modèle ; en
test on appelle la fonction sous-jacente avec un état explicite :

```python
state = {"messages": [], "edc_id": "01234567", "snapshot": case_file, "loaded_at": datetime.now()}
result = list_events.func(state=state, limite=20)
assert "Événements 1 à 20 sur 40" in result
```

Et **un test de schéma**, qui vaut plus qu'il n'en a l'air :

```python
def test_injected_arguments_are_hidden_from_the_model():
    """The model must never be asked to supply the snapshot itself."""
    for tool in ALL_TOOLS:
        schema = tool.args_schema.model_json_schema()
        assert "state" not in schema["properties"]
        assert "tool_call_id" not in schema["properties"]
        assert tool.description and tool.description.strip()
```

Il attrape l'erreur classique — oublier `Annotated[..., InjectedState]` — qui
produirait un modèle essayant d'**inventer** un dossier entier en argument.
On y ajoute une assertion de langue : la description contient au moins un mot
français attendu, ce qui empêche une description anglaise de passer en revue.

**Le chargement sans Oracle** : `load_case_file` est remplacée par une fonction
qui renvoie la fixture (`monkeypatch.setattr`), et une variante qui lève
`oracledb.DatabaseError` pour vérifier le message français du § 6.1 et le fait
que `rafraichir_dossier` **conserve** l'ancien instantané.

### 12.3 Le graphe, sur un modèle simulé

`FakeChatModel` rejoue une liste d'`AIMessage` scriptés (avec ou sans
`tool_calls`) et enregistre les messages reçus.

| Test | Ce qu'il prouve |
|---|---|
| chargement → résumé → réponse | la boucle ReAct tourne et s'arrête |
| trois outils en séquence | les `ToolMessage` reviennent avec le bon `tool_call_id` |
| `charger_dossier` met bien l'état à jour | le `Command` fonctionne (le test le plus important de ce fichier) |
| appel d'un outil de lecture **avant** chargement | message français de guidage, pas d'exception |
| outil qui lève | `handle_tool_errors` renvoie un message, la conversation continue |
| dossier introuvable | message français, `snapshot` reste `None` |
| deux tours avec un checkpointer mémoire | le second tour voit l'instantané du premier |
| le prompt système est bien en tête | et **n'est pas** dupliqué dans l'historique |

Aucun test n'appelle un vrai modèle : les réponses en langage naturel ne sont
pas assertables, seule la **mécanique** l'est.

### 12.4 Ce qui exige un accès réel (`-m live`)

Ces tests sont désélectionnés par défaut et n'ont d'intérêt que dans
l'environnement sécurisé. Ils sont peu nombreux, et chacun vérifie une chose
qu'**aucun test unitaire ne peut prouver**.

| Test | Ce qu'il vérifie |
|---|---|
| `test_thick_mode_initializes` | wallet, `TNS_ADMIN`/`ORACLE_HOME`, mode *thick* |
| `test_five_queries_run` | les 5 SQL s'exécutent réellement sur un id connu (syntaxe `XMLTABLE`/`XMLQuery` comprise) |
| `test_lob_handler_returns_str` | `INFOSPECEVT` arrive en `str` et non en objet LOB |
| **`test_long_event_note_exceeds_256`** | sur un dossier connu pour avoir une note longue : `len(event.details) > 256` — **la seule preuve de bout en bout du § 7** |
| `test_dossier_note_is_stitched` | idem au niveau dossier, via le SQL |
| `test_load_duration` | le chargement complet tient sous un seuil (5 s) sur un dossier moyen |
| `test_vllm_tool_calling` | le scénario du § 8.3 contre le serveur réel |

Les identifiants de dossier utilisés sont lus dans l'environnement
(`LIVE_EDC_ID`, `LIVE_EDC_ID_LONG_NOTE`) — **jamais écrits en dur**, ni dans le
code, ni dans ce document.

---

## 13 — Phase 2 — la voie deep-agents

### 13.1 Ce que la v1 ne sait pas bien faire

L'agent ReAct plafonne sur les questions **larges** : « fais-moi l'audit complet
de ce dossier » demande de lire le bloc-note, parcourir 137 événements par
pages, vérifier chaque bénéficiaire, mesurer les intervalles, puis synthétiser.
Un agent unique y consomme tout son contexte en résultats intermédiaires et perd
le fil de ce qu'il cherchait.

C'est exactement le problème que résout une architecture **superviseur +
sous-agents** : chaque sous-agent travaille dans **son propre contexte** et ne
rend que sa conclusion.

### 13.2 La cible

```
                      ┌──────────────────────────────────────┐
   gestionnaire ─────►│  SUPERVISEUR                         │
                      │  découpe la question, délègue,       │
                      │  synthétise, cite                    │
                      └───┬──────────┬──────────┬────────────┘
                          │          │          │
        ┌─────────────────┘          │          └──────────────────┐
        ▼                            ▼                             ▼
┌──────────────────┐     ┌────────────────────┐      ┌────────────────────────┐
│ agent_chronologie│     │ agent_beneficiaires│      │ agent_conformite       │
│ parcourt, cherche│     │ identités, montants│      │ applique un JEU DE     │
│ date les faits   │     │ soldes, présumés   │      │ RÈGLES scripté         │
└────────┬─────────┘     └─────────┬──────────┘      └───────────┬────────────┘
         │                         │                              │
         └─────────────────────────┴──────────────────────────────┘
                                   │
                     ┌─────────────▼──────────────┐
                     │  MÊME instantané en état    │
                     │  MÊMES 12 outils de lecture │
                     │  (chargé UNE fois)          │
                     └─────────────────────────────┘
```

| Sous-agent | Outils qu'il reçoit | Ce qu'il rend |
|---|---|---|
| `agent_chronologie` | `lister_evenements`, `chercher_evenements`, `detail_evenement`, `statistiques_chronologie`, `libelle_nomenclature` | une chronologie annotée des faits marquants, avec dates et références |
| `agent_beneficiaires` | `lister_beneficiaires`, `detail_beneficiaire`, `synthese_montants`, `lister_evenements` | l'état de chaque bénéficiaire et ce qui bloque son règlement |
| `agent_conformite` | les précédents + `evaluer_regles` (nouveau) | le résultat règle par règle, avec les preuves |

### 13.3 Ce qui, dans la v1, rend cela possible sans réécriture

C'est le point que ce document doit garantir à son relecteur : **aucune décision
de la v1 n'interdit la phase 2.**

| Décision v1 | Ce qu'elle apporte à la phase 2 |
|---|---|
| **L'instantané vit dans l'état, pas dans un outil** (§ 5) | Trois sous-agents lisent le **même** `CaseFile` sans tripler les requêtes Oracle. C'est **la** décision qui rend la phase 2 économiquement viable : sans elle, chaque sous-agent rechargerait le dossier. |
| **Les outils sont des lecteurs purs** (`snapshot.py`) | Ils sont partageables entre agents sans coordination, sans verrou, sans effet de bord. |
| **Deux seuls écrivains** (`charger_dossier`, `rafraichir_dossier`) | La fraîcheur reste pilotée en un point unique : le superviseur charge, les sous-agents lisent. |
| **Les outils renvoient du texte** (§ 6.8) | Un résultat d'outil se transmet tel quel d'un sous-agent à un autre ; avec des `dict`, il faudrait un format d'échange. |
| **Descriptions françaises complètes** | Elles deviennent le contrat des sous-agents sans réécriture : un sous-agent reçoit un sous-ensemble de `ALL_TOOLS`, rien de plus. |
| **`AgentState` en `TypedDict` avec réducteurs explicites** | Il s'étend par ajout de clés ; `messages` garde `add_messages`, les sous-agents reçoivent leur propre liste. |
| **Graphe assemblé à la main** (§ 4.1) | `build_agent()` devient un `build_subagent(tools, prompt)` réutilisé trois fois ; rien à défaire. |
| **Prompt système isolé et sans données** | Il se décline en quatre prompts qui partagent les règles de citation (§ 9.3) et de nomenclature (§ 9.5). |
| **Tests sur fixture d'instantané** (§ 12.2) | Toute la suite reste valable : les outils ne changent pas. |

### 13.4 Ce qui changerait

| Changement | Ampleur |
|---|---|
| Ajouter `deepagents` (ou un superviseur LangGraph écrit à la main) | nouvelle dépendance ; le graphe passe de 2 nœuds à un superviseur + 3 sous-graphes |
| `AgentState` gagne `todos` et `files` (bloc-notes de travail des sous-agents) | ajout de clés + leurs réducteurs ; **le checkpointer doit les sérialiser** — à vérifier tôt |
| Un jeu de règles **scripté**, en données (`rules/conformite.yaml`) | nouveau fichier + l'outil `evaluer_regles(regle_id)` qui l'applique au `CaseFile` en **Python** — pas par le modèle |
| `langgraph.json` expose le superviseur | une ligne |
| Budget de tokens et garde-fous | limite de tours par sous-agent, sinon un audit peut boucler |
| Latence | un audit complet passe de ~10 s à ~60 s : il faut du *streaming* et un message d'attente dans l'interface |

> **Sur le jeu de règles.** Les règles (« une demande de pièces doit être
> relancée sous 30 jours », « un bénéficiaire présumé doit être qualifié sous
> 60 jours ») sont évaluées **en Python sur l'instantané**, pas par le modèle.
> Le modèle rédige la conclusion et cite les preuves ; il ne calcule pas les
> délais. C'est ce qui rend l'audit reproductible et testable — et c'est
> pourquoi la v1 s'interdit déjà de « noter » un dossier (§ 9.2).

**Chemin de migration :** la phase 2 s'ajoute, la v1 reste. Le graphe simple
demeure exposé sous `agent_edc` dans `langgraph.json` pour les questions
ponctuelles ; le superviseur est exposé sous une seconde clé, `audit_edc`.
Les deux partagent le même package, les mêmes outils et les mêmes tests.

---

## 14 — Risques et questions ouvertes

| # | Risque | Gravité | Mitigation |
|---|---|---|---|
| 1 | **Le *tool calling* de vLLM n'est pas fiable** : `tool_calls` vides, arguments mal formés, boucle sur le même outil. | **Bloquant** | (a) Vérifier **avant de coder** (§ 8.3) ; (b) le plus souvent c'est le serveur, pas le modèle : `--enable-auto-tool-choice` + le bon `--tool-call-parser` ; (c) si cela persiste, basculer `vlm.tool_protocol: json` (§ 8.4) — le repli est conçu pour ne rien changer au graphe ; (d) si aucune des deux voies ne tient, le projet doit s'arrêter et changer de modèle : ce n'est pas contournable par du prompt. |
| 2 | **Oracle en mode *thick*** : wallet, `TNS_ADMIN`, `ORACLE_HOME`, client Instant Client absent du serveur qui héberge `langgraph dev`. | **Bloquant** | (a) `uv run agent-edc check` teste la connexion **seule**, avant tout le reste ; (b) `ensure_thick_mode()` protège contre le double appel dans un processus long (§ 5.3) ; (c) l'échec doit produire un message français explicite, jamais une trace Python dans l'interface ; (d) documenter dans le README que le serveur doit tourner **sur une machine ayant le client Oracle** — c'est une contrainte de déploiement, pas de code. |
| 3 | **Instantané obsolète** : l'agent affirme un fait périmé. | Moyenne | (a) Âge affiché au-delà de 30 min (§ 5.5) ; (b) règle explicite dans le prompt (§ 9.4) ; (c) `rafraichir_dossier` annonce le **delta** ; (d) `snapshot_age_s` en métadonnée de trace permet l'audit a posteriori. |
| 4 | **Très gros dossier** (>1 000 événements) : saturation du contexte, latence. | Moyenne | (a) Aucun outil ne renvoie tout : pagination bornée à 50, résumé à 5 événements ; (b) `statistiques_chronologie` donne la forme du dossier pour ~80 tokens ; (c) au-delà de 1 000 événements, le résumé ajoute : « dossier volumineux — privilégie `chercher_evenements` et les filtres de date » ; (d) `result_chars` en trace pour détecter les sorties qui gonflent ; (e) si un dossier dépasse la mémoire raisonnable (>5 000 événements), `charger_dossier` le signale et propose de charger une fenêtre de dates — **à implémenter seulement si le cas se présente**. |
| 5 | **Dérive des nomenclatures** face à `files_late_fee`. | Faible | Test de parité `-m parity` + en-têtes de provenance + table des divergences assumées (§ 2.4). Un code inconnu se voit de toute façon (« sous-événement inconnu ») et le prompt interdit de l'interpréter (§ 9.5). |
| 6 | **Le modèle répond en anglais** malgré un prompt français. | Faible mais visible | (a) Règle de langue en **ouverture et en clôture** du prompt (§ 9.1, 9.6) ; (b) toutes les descriptions d'outils et **tous les retours d'outils** sont en français — c'est le contexte immédiat qui pèse le plus ; (c) les noms d'outils sont français ; (d) si le problème persiste, ajouter au nœud `agent` une vérification bon marché (détection de langue sur la réponse finale, une seule relance avec « Réponds en français. ») — **à n'ajouter qu'après constat**, pas par anticipation. |
| 7 | **Fuite de données personnelles dans les traces.** | Élevée si négligée | § 10.5 : pas de `raw`, fonction de masquage, erreurs Oracle non remontées, et traçage **interdit** hors instance interne. Défaut : `langfuse.enabled: false`. |
| 8 | **Appels d'outils parallèles** : un outil lit un état pré-chargement (§ 4.5). | Faible | `charger_dossier` renvoie déjà le résumé ; règle de prompt ; message de guidage plutôt qu'erreur. Le test « outil de lecture avant chargement » (§ 12.3) verrouille le comportement. |
| 9 | **Le modèle cite une ligne tronquée** de liste comme si c'était le texte complet. | Moyenne | Le `…` est visible, la ligne rappelle l'outil de détail, et le prompt l'impose (§ 9.4). Métadonnée `truncated` en trace pour vérifier après coup. |
| 10 | **L'agent se prononce au-delà de ses données** (documents, Numéa, contrats). | Moyenne | Règle explicite de périmètre (§ 9.4, dernier point) et non-objectifs assumés (§ 1.4). À vérifier dès les premiers essais réels : c'est un travers fréquent. |

### Questions ouvertes (à trancher avec les métiers ou à l'intégration)

| Question | Pourquoi elle n'est pas tranchée ici | Quand la trancher |
|---|---|---|
| Quels **seuils** font qu'un dossier est « mal géré » (30 j sans relance ? 60 j ? 90 j ?) | C'est une décision métier, pas technique. La v1 décrit sans noter (§ 9.2). | Avant la phase 2 — c'est le contenu du jeu de règles (§ 13.4). |
| Faut-il **exclure** certains sous-types d'événements comme le faisaient les scripts Eckert (`IGNORE_SS_EVT`) ? | Le filtrage historique servait un autre objectif ; filtrer ici pourrait masquer une preuve. La v1 **ne filtre rien**. | Après les premiers retours de gestionnaires. |
| Le regroupement **session Langfuse** fonctionne-t-il via `RunnableConfig` sous `langgraph dev` ? | Dépend de la version de l'hôte. | À la première trace réelle (§ 10.3). |
| Quelle **rétention** pour les checkpoints SQLite (données personnelles au repos) ? | Question de gouvernance, pas d'architecture. | Avant toute mise à disposition au-delà du poste de développement. |
| Faut-il remonter le correctif bloc-note vers `files_late_fee` ? | Ce dépôt est hors périmètre de cette mission. | À proposer en ticket séparé — le pipeline y perd aussi du texte aujourd'hui. |

---

## Annexe A — `.env.example` et `config.yaml`

**`.env`** (copié depuis `.env.example`, ignoré par git) ne contient que les
secrets et les adresses :

| Variable | Rôle | Défaut |
|---|---|---|
| `EDC_DSN` | DSN Oracle de la base E-décès | — |
| `EDC_USER` / `EDC_PASSWORD` | identifiants Oracle | — |
| `EDC_TNS_ADMIN` | chemin du wallet | — |
| `EDC_ORACLE_HOME` | chemin du client Oracle (mode *thick*) | — |
| `VLM_BASE_URL` | endpoint OpenAI-compatible du serveur vLLM | — |
| `VLM_API_KEY` | clé d'API (vLLM accepte toute valeur) | `EMPTY` |
| `LANGFUSE_HOST` | URL de l'instance Langfuse — **interne uniquement** (§ 10.5) | — |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | clés Langfuse | — |

**`config.yaml`** (versionné) porte les réglages non sensibles, une section par
sous-config. Il **refuse** les clés ci-dessus et toute clé inconnue (erreur
explicite au démarrage). Priorité : variable d'environnement > `.env` >
`config.yaml` > défaut ; toute clé reste surchargeable par sa variable
(`VLM_REASONING_EFFORT=low`, `HISTORY_COMPACTION=true`…).

| Section.clé | Rôle | Valeur livrée |
|---|---|---|
| `vlm.model` | nom du modèle servi (cf. `GET /v1/models`) | `Qwen/Qwen3.8-27B` |
| `vlm.enable_thinking` / `vlm.reasoning_effort` | raisonnement (§ 8.1) ; effort `xhigh`/`medium`/`low` | `true` / `medium` |
| `vlm.temperature`, `vlm.max_tokens`, `vlm.timeout` | génération | `1.0`, `16384`, `300` |
| `vlm.parallel_tool_calls` | un seul appel d'outil par tour si `false` (§ 4.5) | `false` |
| `vlm.tool_protocol` | `native` ou `json` (repli § 8.4) | `native` |
| `vlm.extra_body` | tout paramètre vLLM, transmis tel quel | `top_p 0.95, top_k 20, min_p 0` |
| `history.*` | rappels d'outils et compactage (§ 4.6) | `3`, `false`, `60000`, `4` |
| `langfuse.enabled` | interrupteur maître du traçage | `false` |
| `agent.summary_events` | nombre d'événements listés dans le résumé (§ 5.4) | `5` |
| `agent.page_size` / `agent.max_page_size` | pagination et plafond de `limite` (§ 6.1) | `20` / `50` |
| `agent.stale_after_minutes` | âge à partir duquel la fraîcheur est signalée (§ 5.5) | `30` |
| `agent.checkpoint_db` | fichier SQLite du REPL (§ 11.2) | `.checkpoints/agent_edc.sqlite` |
| `edc.pool_min` / `edc.pool_max` | pool de connexions Oracle | `0` / `4` |
| `logging.log_level` / `logging.log_dir` | journaux | `INFO` / `logs` |

Correspondance avec `config.py` : sous-configs `pydantic-settings` (préfixes
`EDC_`, `VLM_`, `LANGFUSE_`, `AGENT_`, `HISTORY_`) dérivées de `YamlSettings`,
qui insère la section `config.yaml` entre `.env` et les défauts.

## Annexe B — Glossaire EDC

| Terme | Signification |
|---|---|
| **EDC / E-décès / ESD** | L'application et la base Oracle de gestion des dossiers décès. `ESD…` est le préfixe des tables. |
| **`IDDOSSIER`** | L'identifiant « métier » du dossier, 8 chiffres. C'est ce que dit le gestionnaire. Attention : Excel supprime volontiers le zéro de tête. |
| **`REFDOSSIER`** | La clé technique interne, différente de `IDDOSSIER` ; sert aux jointures. |
| **Intercalaire** | Un « onglet » du dossier, rattaché à une personne ou à un rôle. `TYPEINTERCALAIRE` : `'1'` Assuré, `'2'` Bénéficiaire, `'6'` Bénéficiaire présumé. |
| **`REFINTERCALAIRE`** | La clé d'un intercalaire ; relie un événement à un bénéficiaire (§ 2.3). |
| **Événement** | Une ligne d'`ESDEVENEMENT` : un acte de gestion daté, typé, souvent commenté. |
| **Bloc-note** | Le commentaire libre d'un gestionnaire, sur le dossier ou sur un événement. **Tronqué autour de 256 caractères, suite dans le XML** (§ 7). |
| **`INFOSPEC*`** | Les CLOB XML (`INFOSPECDOSSIER`, `INFOSPECEVT`, `INFOSPECINTERCALAIRE`) au format `<vo nom="racine"><string nom="champ">…</string></vo>`, lus par recherche récursive sur l'attribut `nom`. |
| **`blocNoteExtensible`** | Le nœud XML contenant **la suite** d'un bloc-note tronqué. |
| **Ordonnancement** | Une opération de règlement, dans `listeOrdonnancements` du XML d'intercalaire. Montants **en centimes**. |
| **Bénéficiaire présumé** | Un bénéficiaire identifié mais pas encore qualifié — une étape de gestion qui reste à faire. |
| **Réseau** | `CRITEREDOSSIER1` : `'0'` Trésor, `'4'` Poste, `'8'` Caisse d'Épargne. |
| **Motif FT / motif d'attente** | Motif de fin de traitement (`MOTIF_FT`) ou de mise en attente (`MOTIF_ATTENTE`, sous-type `M8`). |
| **Intérêts de retard** | Les intérêts dus quand le règlement d'un capital décès dépasse le délai légal — l'enjeu financier qui motive toute cette famille de projets. |
