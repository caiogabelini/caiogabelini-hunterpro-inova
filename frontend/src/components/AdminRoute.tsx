import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

/**
 * Guard adicional pra rotas só-admin (hoje só /busca-leads) -- espera
 * já estar dentro de <ProtectedRoute> (não reimplementa a checagem de
 * "está logado", só a de "é admin"; composição de dois guards
 * single-purpose, mesmo padrão de <ProtectedRoute> + <AuthenticatedLayout>
 * já usado em toda rota autenticada, ver App.tsx). Um usuário "client"
 * que digitar a URL direto é redirecionado pro Kanban ("/"), não pro
 * /login -- ele JÁ está autenticado, só não tem permissão pra essa tela
 * específica (mesma distinção 401 vs. 403 que o backend já faz entre
 * `get_current_user` e `require_admin`).
 *
 * ⚠️ Isso é só UX, não uma fronteira de segurança de verdade -- a
 * autorização real continua sendo o `require_admin` do backend (403 pra
 * "client" em qualquer chamada a /api/admin/*), que é o que de fato
 * impede o acesso aos dados. Esconder a rota aqui só evita que alguém
 * sem permissão veja uma tela cheia de erros 403 em vez de ser mandado
 * de volta pro Kanban.
 */
export function AdminRoute({ children }: { children: ReactNode }) {
  const { role } = useAuth();
  if (role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
