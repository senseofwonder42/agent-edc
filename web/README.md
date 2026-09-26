# Interface web agent-edc

Interface [assistant-ui](https://www.assistant-ui.com/) branchée sur le serveur Aegra
(runtime `@assistant-ui/react-langchain`, hook `useStream`). Contexte et choix :
[../docs/MIGRATION-AEGRA-ASSISTANT-UI.md](../docs/MIGRATION-AEGRA-ASSISTANT-UI.md).

```bash
cp .env.example .env.local   # LANGGRAPH_API_URL=http://localhost:2026, assistant agent_edc
npm install
npm run dev                  # http://localhost:3000 (ou le premier port libre)
```

Le navigateur ne parle qu'à Next : la route [app/api/[..._path]/route.ts](app/api/[..._path]/route.ts)
relaie les appels vers Aegra côté serveur et refuse les requêtes d'une autre origine.
Ce contrôle n'est **pas** une authentification (voir la migration, « Suite prévue »).

| Fichier | Rôle |
|---|---|
| `app/MyRuntimeProvider.tsx` | Connexion au serveur (threads, streaming) |
| `app/threadListAdapter.ts` | Liste des conversations lue dans Aegra (titre = première question, archivage et renommage dans les métadonnées du fil). Sans lui, la liste serait vide à chaque rechargement |
| `app/page.tsx` | Liste des conversations + fil, suggestions d'accueil |
| `components/assistant-ui/elements/` | Composants générés par assistant-ui (textes traduits en français) ; `tool-fallback.aui.tsx` affiche les appels d'outils et leurs résultats |
