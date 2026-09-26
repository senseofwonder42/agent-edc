"use client";

import { useState, type FormEvent } from "react";
import { useAui } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const EDC_ID = /^\d{8}$/;

/** New-conversation screen: one conversation = one dossier (8-digit id). */
export function DossierWelcome() {
  const aui = useAui();
  const [edcId, setEdcId] = useState("");
  const valid = EDC_ID.test(edcId);

  const open = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    aui.thread().append(`Dossier ${edcId} : fais-moi un résumé du dossier.`);
  };

  return (
    <div className="mb-6 flex flex-col gap-4 px-2">
      <p className="text-2xl font-medium tracking-tight">
        Quel dossier E-décès voulez-vous examiner ?
      </p>
      <form onSubmit={open} className="flex items-center gap-2">
        <Input
          value={edcId}
          onChange={(e) => setEdcId(e.target.value.replace(/\D/g, "").slice(0, 8))}
          inputMode="numeric"
          autoComplete="off"
          placeholder="8 chiffres"
          aria-label="Identifiant du dossier"
          className="w-56 font-mono tracking-wider"
        />
        <Button type="submit" disabled={!valid}>
          Ouvrir le dossier
        </Button>
      </form>
      <p className="text-muted-foreground text-sm">
        Ou posez directement une question ci-dessous, en citant l’identifiant du dossier.
      </p>
    </div>
  );
}
