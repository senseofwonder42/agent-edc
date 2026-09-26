// French labels for the agent's tools (src/agent_edc/agent/tools), shown in
// the conversation instead of the raw tool name and JSON arguments.

type Args = Record<string, unknown>;

const LABELS: Record<string, (a: Args) => string> = {
  charger_dossier: (a) => `Chargement du dossier ${a.edc_id ?? ""}`.trim(),
  rafraichir_dossier: () => "Rafraîchissement du dossier",
  resume_dossier: () => "Résumé du dossier",
  bloc_note_dossier: () => "Lecture du bloc-note",
  lister_evenements: (a) => {
    const range =
      a.date_min || a.date_max ? ` (${a.date_min ?? "…"} → ${a.date_max ?? "…"})` : "";
    return `Liste des événements${range}`;
  },
  chercher_evenements: (a) => `Recherche dans les événements : « ${a.requete ?? ""} »`,
  detail_evenement: (a) => `Détail de l’événement ${a.evenement_id ?? ""}`.trim(),
  statistiques_chronologie: () => "Statistiques de la chronologie",
  lister_beneficiaires: () => "Liste des bénéficiaires",
  detail_beneficiaire: (a) => `Détail du bénéficiaire ${a.beneficiaire_id ?? ""}`.trim(),
  synthese_montants: () => "Synthèse des montants",
  libelle_nomenclature: (a) => `Libellé du code ${a.code ?? ""} (${a.table ?? "nomenclature"})`,
};

export function parseArgs(argsText: string | undefined): Args {
  try {
    const parsed: unknown = JSON.parse(argsText ?? "");
    return parsed && typeof parsed === "object" ? (parsed as Args) : {};
  } catch {
    return {}; // still streaming: incomplete JSON
  }
}

export function toolLabel(toolName: string, argsText: string | undefined): string {
  const label = LABELS[toolName];
  return label ? label(parseArgs(argsText)) : toolName;
}

export function hasArgs(argsText: string | undefined): boolean {
  return Object.keys(parseArgs(argsText)).length > 0;
}
