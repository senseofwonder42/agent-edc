"use client";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";
import { DossierBanner } from "./DossierBanner";
import { DossierWelcome } from "./DossierWelcome";

export default function Home() {
  return (
    <div className="flex h-dvh">
      <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-r p-3">
        <div className="px-2 pt-1">
          <p className="font-semibold">Agent E-décès</p>
          <p className="text-muted-foreground text-xs">Consultation en lecture seule</p>
        </div>
        <ThreadList />
      </aside>
      <main className="flex min-w-0 flex-grow flex-col">
        <DossierBanner />
        <div className="min-h-0 flex-1">
          <Thread components={{ Welcome: DossierWelcome }} />
        </div>
      </main>
    </div>
  );
}
