import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AdminRoute } from "./components/AdminRoute";
import { AuthenticatedLayout } from "./components/AuthenticatedLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AuthProvider } from "./context/AuthContext";
import { BuscaLeadsPage } from "./pages/BuscaLeadsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { KanbanPage } from "./pages/KanbanPage";
import { LeadDossierPage } from "./pages/LeadDossierPage";
import { ListaLeadsPage } from "./pages/ListaLeadsPage";
import { LoginPage } from "./pages/LoginPage";

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <AuthenticatedLayout>
                  <KanbanPage />
                </AuthenticatedLayout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/leads"
            element={
              <ProtectedRoute>
                <AuthenticatedLayout>
                  <ListaLeadsPage />
                </AuthenticatedLayout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/leads/:id"
            element={
              <ProtectedRoute>
                <AuthenticatedLayout>
                  <LeadDossierPage />
                </AuthenticatedLayout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <AuthenticatedLayout>
                  <DashboardPage />
                </AuthenticatedLayout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/busca-leads"
            element={
              <ProtectedRoute>
                <AdminRoute>
                  <AuthenticatedLayout>
                    <BuscaLeadsPage />
                  </AuthenticatedLayout>
                </AdminRoute>
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
