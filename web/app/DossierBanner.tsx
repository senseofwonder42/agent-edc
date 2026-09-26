"use client";

import { useEffect, useState } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { useLangChainState } from "@assistant-ui/react-langchain";
import { RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Same threshold as agent.stale_after_minutes (config.yaml, § 5.5).
const STALE_AFTER_MINUTES = 30;

const formatAge = (minutes: number) => {
  if (minutes < 1) return "à l’instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  return `il y a ${Math.floor(hours / 24)} j`;
};

const useNow = (intervalMs: number) => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
};

/** Dossier loaded in this conversation (thread state) and the snapshot's age. */
export function DossierBanner() {
  const aui = useAui();
  const edcId = useLangChainState<string | null>("edc_id", null);
  const loadedAt = useLangChainState<string | null>("loaded_at", null);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const now = useNow(30_000);

  if (!edcId) return null;

  const minutes = loadedAt
    ? Math.max(0, Math.floor((now - new Date(loadedAt).getTime()) / 60_000))
    : null;
  const stale = minutes !== null && minutes > STALE_AFTER_MINUTES;

  return (
    <div className="flex items-center justify-between gap-4 border-b px-4 py-2 text-sm">
      <div className="flex items-baseline gap-2">
        <span className="font-medium">Dossier {edcId}</span>
        {minutes !== null && (
          <span className={cn("text-muted-foreground", stale && "text-amber-600")}>
            · données chargées {formatAge(minutes)}
          </span>
        )}
      </div>
      <Button
        variant="ghost"
        size="sm"
        disabled={isRunning}
        onClick={() => aui.thread().append("Rafraîchis le dossier.")}
      >
        <RefreshCwIcon className="size-4" />
        Rafraîchir
      </Button>
    </div>
  );
}
