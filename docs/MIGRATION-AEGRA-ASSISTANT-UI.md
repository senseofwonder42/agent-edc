# Migration : Aegra + assistant-ui

> Passage de `langgraph dev` + Agent Chat UI à **Aegra** (serveur) + **assistant-ui** (interface).
> Branche `feat/aegra-assistant-ui`. Le graphe, les outils et les prompts n'ont pas changé.

## 1. Pourquoi

| Avant | Limite | Après |
|---|---|---|
| `langgraph dev` | Serveur **de développement**. L'Agent Server officiel (LangSmith Deployments) ne s'auto-héberge en production qu'avec une **licence entreprise**. | **Aegra** : Apache 2.0, même API (Agent Protocol), PostgreSQL, auth configurable (JWT / OAuth), auto-hébergé. |
| Agent Chat UI | Outil de démo générique : en anglais, sans auth, peu personnalisable. | **assistant-ui** : composants React modifiables dans le dépôt (`web/`), runtime LangGraph officiel. |

On ne remplace **pas** LangGraph. On remplace l'hôte qui le sert et l'écran qui l'affiche.

## 2. Architecture

```
 Navigateur ──▶ Next.js (web/, port 3000)  ──▶  Aegra (port 2026)  ──▶  graphe LangGraph (src/)
               proxy /api/[..._path]            API Agent Protocol       nœuds, outils, Oracle, vLLM
               (côté serveur)                   threads, runs, SSE
                                                │
                                                └──▶ PostgreSQL (checkpoints + fils)
```

- **Librairie `langgraph`** (inchangée) : définit et exécute le graphe (`StateGraph`, `ToolNode`, état, `Command`).
- **Serveur (Aegra)** : lit `aegra.json`, importe `build.py:graph`, **injecte son checkpointer PostgreSQL**, expose l'API HTTP, relaie le streaming.
- **Interface (assistant-ui)** : ne parle qu'à Next ; la route proxy appelle Aegra côté serveur.

`graph` était déjà compilé sans checkpointer (`build_agent(checkpointer=None)`) : le code du graphe n'a donc pas bougé.

## 3. Ce qui change / ce qui ne change pas

| | Avant | Après |
|---|---|---|
| Python | 3.11 | **3.12** (exigé par Aegra) |
| Commande serveur | `uv run langgraph dev` | `uv run aegra dev` (prod : `uv run aegra serve`) |
| Port serveur | 2024 | **2026** |
| Configuration serveur | `langgraph.json` | **`aegra.json`** (même graphe, même id `agent_edc`) |
| Persistance des fils | fichiers `.langgraph_api/` | **PostgreSQL** (`docker-compose.yml`, port local **5433**) |
| Interface | Agent Chat UI (dépôt externe) | **`web/`** (Next.js + assistant-ui) |
| Variables interface | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_ASSISTANT_ID` | `LANGGRAPH_API_URL`, `NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID` |

**Inchangés** : tout `src/agent_edc/` (hors deux docstrings), `config.yaml`, le REPL `agent-edc chat`,
les tests existants, Langfuse, `langgraph.json` / `langgraph dev` (toujours utilisables avec Studio).

**Ajoutés** : `aegra.json`, `docker-compose.yml`, variables `POSTGRES_*` / `AUTH_TYPE` dans
`.env.example`, `tests/test_persistence.py`, dossier `web/`, ce document.

## 4. Démarrer

```bash
uv sync
cp .env.example .env              # renseigner EDC_*, VLM_*, et POSTGRES_PASSWORD (mot de passe local)
uv run aegra dev                  # démarre PostgreSQL via Docker puis le serveur (http://127.0.0.1:2026)
curl localhost:2026/health        # {"status":"healthy", ...}

cd web
cp .env.example .env.local        # LANGGRAPH_API_URL=http://localhost:2026, assistant agent_edc
npm install
npm run dev                       # http://localhost:3000 (ou le premier port libre)
```

La base est un `postgres:17` standard. Aegra recommande l'image `pgvector/pgvector`, mais
pgvector ne sert qu'à l'index vectoriel du store, non utilisé ici (pas de bloc `store.index`
dans `aegra.json`). Avec une base déjà démarrée ailleurs : `uv run aegra dev --no-db-check`.
SQLite n'est pas possible : Aegra ne supporte que PostgreSQL (schéma `JSONB`, `asyncpg`,
`AsyncPostgresSaver`) ; il reste utilisé par le REPL (`SqliteSaver`).

## 5. Points de vigilance

1. **Client Oracle sur la machine d'Aegra.** Aegra importe le graphe dans son propre processus :
   l'API doit tourner là où se trouvent Instant Client et le wallet (`EDC_TNS_ADMIN`,
   `EDC_ORACLE_HOME`). C'est pourquoi `docker-compose.yml` ne contient que PostgreSQL.
2. **Python 3.12 sur le serveur de déploiement.** À vérifier avant la mise en service.
3. **Sérialisation de l'instantané.** Aegra utilise le sérialiseur par défaut de LangGraph
   (le REPL, lui, restreint msgpack aux classes du projet). Le défaut relit bien `CaseFile`
   (`tests/test_persistence.py`). Ne pas activer `LANGGRAPH_STRICT_MSGPACK=true` côté serveur
   sans déclarer ces classes.
4. **Pas d'authentification pour l'instant.** `AUTH_TYPE=noop` ; le proxy Next bloque les
   requêtes d'une autre origine mais n'identifie personne. Ne pas exposer au-delà du poste
   de développement en l'état (données personnelles).
5. **Port 5433.** Choisi pour ne pas entrer en conflit avec le PostgreSQL d'une instance
   Langfuse locale (5432).

## 6. Vérifications faites

- `uv run pytest` : 123 tests verts (dont le nouveau test de sérialisation) ; `ruff check` OK.
- Aegra : `/health` OK, l'assistant `agent_edc` est enregistré, création et liste des fils ;
  un fil et son état sont relus **après redémarrage** d'Aegra (PostgreSQL).
- Une exécution passe par le graphe jusqu'à l'appel du modèle (sans vLLM sur le poste :
  le message d'erreur français de la configuration remonte bien).
- `web/` : `npm run build` et `npm test` (tests du proxy) OK ; page en français ; fils créés
  et listés **à travers le proxy** vers Aegra.
- Liste des conversations : le template ne la branche que sur Assistant Cloud (sinon liste
  en mémoire, vide à chaque rechargement) ; `web/app/threadListAdapter.ts` la lit dans Aegra.
  Vérifié dans Chrome : une conversation de démonstration (données fictives) se rouvre avec ses
  appels d'outils dépliables et leurs résultats.

**Reste à vérifier dans l'environnement sécurisé** (Oracle + vLLM) : conversation complète
dans l'interface (chargement du dossier, appels d'outils affichés, citations), reprise d'un
fil contenant un instantané réel, `uv run pytest -m live`.

## 7. Suite prévue

- Authentification Aegra (JWT / OAuth) branchée sur l'annuaire interne, et contrôle de
  session dans le proxy Next.
- Champ dédié « identifiant dossier » à l'ouverture d'une conversation.
- Rendu des citations (liens vers les événements) et affichage du raisonnement.
- Image Docker de l'API incluant Instant Client Oracle (`aegra up`).
