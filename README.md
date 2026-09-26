# agent-edc

Agent conversationnel **en lecture seule** sur un dossier E-décès : le gestionnaire
donne un identifiant à 8 chiffres et pose ses questions en français ; l'agent répond
avec des citations datées tirées d'EDC.

La conception complète est dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Démarrage express

```bash
uv sync
cp .env.example .env                    # secrets : EDC_*, VLM_BASE_URL, clés Langfuse
$EDITOR config.yaml                     # modèle, raisonnement, génération, réglages agent
uv run agent-edc check                  # Oracle + vLLM + appel d'outils — à faire en premier
uv run agent-edc chat 01234567          # REPL terminal
uv run aegra dev                        # serveur Aegra + PostgreSQL sur http://127.0.0.1:2026
cd web && cp .env.example .env.local && npm install && npm run dev   # interface web
```

Serveur [Aegra](https://github.com/aegra/aegra) (Docker requis pour PostgreSQL en local),
interface [assistant-ui](https://www.assistant-ui.com/) dans [web/](web/).
Pourquoi ce choix et ce qui a changé : [docs/MIGRATION-AEGRA-ASSISTANT-UI.md](docs/MIGRATION-AEGRA-ASSISTANT-UI.md).
`uv run langgraph dev` (Studio, port 2024) reste disponible en développement.

## Contraintes de déploiement

- Le serveur (Aegra, **Python ≥ 3.12**) doit tourner sur une machine disposant du **client Oracle** (mode *thick*,
  wallet via `EDC_TNS_ADMIN` / `EDC_ORACLE_HOME`).
- Serveur vLLM pour Qwen3.8-27B (`--reasoning-parser` indispensable, sinon la réflexion
  arrive dans la réponse) ; si l'appel d'outils reste peu fiable : `tool_protocol: json` (§ 8.4).

  ```bash
  vllm serve Qwen/Qwen3.8-27B --max-model-len 262144 --reasoning-parser qwen3 \
    --enable-auto-tool-choice --tool-call-parser qwen3_xml
  ```

  Raisonnement : `vlm.enable_thinking` et `vlm.reasoning_effort` (`xhigh` | `medium` | `low`)
  dans `config.yaml`. Attention : l'analyseur vLLM peut prendre pour un vrai appel une
  syntaxe d'appel d'outil citée dans la réflexion (issue vLLM #58147) ; un outil inconnu
  est renvoyé au modèle comme une erreur.
- Longues conversations : section `history` de `config.yaml` — anciens résultats d'outils
  remplacés par un rappel, et compactage optionnel (`compaction: true`) qui résume les
  anciens tours ; l'historique complet reste dans l'état (§ 4.6).
- `langfuse.enabled: true` uniquement contre une instance **interne** (données personnelles).

## Tests

```bash
uv run pytest              # sans Oracle, sans vLLM, sans réseau
uv run pytest -m parity    # nomenclatures vs files_late_fee — avant chaque livraison
uv run pytest -m live      # environnement sécurisé : Oracle + wallet + vLLM
```
