import "./globals.css";

import { cn } from "@/lib/utils";
import { Montserrat } from "next/font/google";
import { MyRuntimeProvider } from "./MyRuntimeProvider";
import { Suspense } from "react";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent E-décès",
  description: "Consultation d'un dossier E-décès (lecture seule)",
};

const montserrat = Montserrat({ subsets: ["latin"] });

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <MyRuntimeProvider>
      <html lang="fr">
        <body className={cn(montserrat.className, "h-dvh")}>
          <Suspense>{children}</Suspense>
        </body>
      </html>
    </MyRuntimeProvider>
  );
}
