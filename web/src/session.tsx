// Sesión del usuario. Si la API tiene autenticación activada, se inicia sesión con usuario y
// contraseña (token JWT); si no (desarrollo), alcanza con indicar un nombre para firmar acciones.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError, request, setAuthToken } from "./api";

export type Role = "viewer" | "operator" | "planner" | "engineer" | "admin";

export interface User {
  username: string;
  name: string;
  roles: Role[];
}

interface SessionState {
  user: User | null;
  token: string | null;
  authEnabled: boolean;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  setGuestName: (name: string) => void;
  logout: () => void;
  can: (role: Role) => boolean;
}

const SessionContext = createContext<SessionState | null>(null);
const TOKEN_KEY = "mvt.token";
const GUEST_KEY = "mvt.guest";

function readStorage(storage: "local" | "session", key: string): string | null {
  try {
    return (storage === "local" ? window.localStorage : window.sessionStorage).getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(storage: "local" | "session", key: string, value: string | null) {
  try {
    const s = storage === "local" ? window.localStorage : window.sessionStorage;
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    /* almacenamiento no disponible */
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [authEnabled, setAuthEnabled] = useState(false);
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      let enabled = false;
      try {
        enabled = (await request<{ enabled: boolean }>("GET", "/api/v1/auth/config")).enabled;
      } catch {
        enabled = false; // API sin módulo de autenticación
      }
      setAuthEnabled(enabled);
      if (enabled) {
        const saved = readStorage("session", TOKEN_KEY);
        if (saved) {
          setAuthToken(saved);
          try {
            setUser(await request<User>("GET", "/api/v1/auth/me"));
            setToken(saved);
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 401)) console.warn(e);
            setAuthToken(null);
            writeStorage("session", TOKEN_KEY, null);
          }
        }
      } else {
        const guest = readStorage("local", GUEST_KEY);
        if (guest) setUser({ username: guest, name: guest, roles: ["admin"] });
      }
      setReady(true);
    })();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await request<{ access_token: string; user: User }>("POST", "/api/v1/auth/login", { username, password });
    setAuthToken(res.access_token);
    writeStorage("session", TOKEN_KEY, res.access_token);
    setToken(res.access_token);
    setUser(res.user);
  }, []);

  const setGuestName = useCallback((name: string) => {
    writeStorage("local", GUEST_KEY, name);
    setUser({ username: name, name, roles: ["admin"] });
  }, []);

  const logout = useCallback(() => {
    setAuthToken(null);
    writeStorage("session", TOKEN_KEY, null);
    writeStorage("local", GUEST_KEY, null);
    setToken(null);
    setUser(null);
  }, []);

  const can = useCallback(
    (role: Role) => !!user && (user.roles.includes("admin") || user.roles.includes(role)),
    [user],
  );

  return (
    <SessionContext.Provider value={{ user, token, authEnabled, ready, login, setGuestName, logout, can }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession fuera de SessionProvider");
  return ctx;
}

export function LoginScreen() {
  const { authEnabled, login, setGuestName } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(undefined);
    if (!authEnabled) {
      if (username.trim()) setGuestName(username.trim());
      return;
    }
    setBusy(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Usuario o contraseña incorrectos" : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form className="panel" onSubmit={submit} style={{ width: "min(380px, 100%)", padding: 24, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="brand" style={{ padding: "0 0 16px", margin: "0 0 4px" }}>
          <div className="brand-name">
            Mine<span>Vision</span> Twin
          </div>
          <div className="brand-site">Mantenimiento predictivo</div>
        </div>
        <label className="field">
          {authEnabled ? "Usuario" : "Tu nombre (para registrar tus acciones)"}
          <input className="input" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        {authEnabled && (
          <label className="field">
            Contraseña
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
        )}
        {error && <div className="error-box">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy} style={{ justifyContent: "center", padding: "9px 12px" }}>
          {busy ? "Ingresando…" : "Ingresar"}
        </button>
        {!authEnabled && <div className="muted" style={{ fontSize: 12 }}>Modo desarrollo: la API no tiene autenticación activada.</div>}
      </form>
    </div>
  );
}
