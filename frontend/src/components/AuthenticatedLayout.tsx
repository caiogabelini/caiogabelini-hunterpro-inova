import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import "./AuthenticatedLayout.css";

/**
 * Composição sidebar + conteúdo pra toda tela autenticada (Kanban,
 * Dossiê -- Login fica de fora, sem sidebar). Deliberadamente separado
 * de ProtectedRoute (components/ProtectedRoute.tsx): aquele componente
 * cuida só do guard de autenticação (redireciona pro /login sem token),
 * este cuida só do chrome visual -- os dois se compõem em App.tsx, não
 * concentram as duas responsabilidades num componente só.
 */
export function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <main className="app-content">{children}</main>
    </div>
  );
}
