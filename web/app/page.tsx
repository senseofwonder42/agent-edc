"use client";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";
import { useAui, AuiProvider, AuiConfig, Suggestions } from "@assistant-ui/react";

function ThreadWithSuggestions() {
  const aui = useAui();
  const config = AuiConfig({
    suggestions: Suggestions([
      {
        title: "Résumé du dossier",
        label: "état, montants et derniers événements",
        prompt: "Dossier 01234567 : fais-moi un résumé.",
      },
      {
        title: "Chronologie",
        label: "délais et périodes sans événement",
        prompt: "Dossier 01234567 : quels ont été les plus longs délais sans événement ?",
      },
    ]),
  });
  return (
    <AuiProvider extends={aui} config={config}>
      <Thread />
    </AuiProvider>
  );
}

export default function Home() {
  return (
    <div className="flex h-dvh">
      <div className="max-w-md">
        <ThreadList />
      </div>
      <div className="flex-grow">
        <ThreadWithSuggestions />
      </div>
    </div>
  );
}
