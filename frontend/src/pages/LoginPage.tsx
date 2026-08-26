import { Building2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { mensagemDeErroDeLogin } from "../erroLogin";
import "./LoginPage.css";

export function LoginPage() {
  const { isAuthenticated, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  // já logado (ex.: veio de /login direto pela URL) -- manda pro Kanban,
  // ou pra rota que originalmente exigiu login (redirect da ProtectedRoute).
  if (isAuthenticated) {
    const destino = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={destino} replace />;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setCarregando(true);
    try {
      await login(email, senha);
      const destino = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(destino, { replace: true });
    } catch (e) {
      // `catch (e)`, não `catch {}`: a versão anterior descartava o erro
      // e fixava a mensagem de credenciais, escondendo o 429 do limite
      // de tentativas -- ver src/erroLogin.ts.
      setErro(mensagemDeErroDeLogin(e));
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-icon">
          <Building2 size={22} />
        </div>
        <div className="login-eyebrow">HunterPro</div>
        <h1 className="login-title">Inova Contabilidade</h1>
        <p className="login-subtitle">Entre com sua conta pra acessar o pipeline de leads.</p>

        <label className="login-field">
          <span>E-mail</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>

        <label className="login-field">
          <span>Senha</span>
          <input
            type="password"
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {erro && <p className="login-error">{erro}</p>}

        <button type="submit" className="login-submit" disabled={carregando}>
          {carregando ? "Entrando..." : "Entrar"}
        </button>
      </form>
    </div>
  );
}
