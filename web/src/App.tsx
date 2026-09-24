import { useEffect, useState } from "react";

import { api } from "./api";
import { eventLabel } from "./components/Common";
import { useHashRoute, href, useResource, useStoredState } from "./hooks";
import { LiveProvider, useLive, useLiveVersion, type LiveMessage } from "./live";
import { AlarmsPage } from "./pages/AlarmsPage";
import { AssetPage } from "./pages/AssetPage";
import { EventsPage } from "./pages/EventsPage";
import { Overview } from "./pages/Overview";
import { RecommendationsPage } from "./pages/RecommendationsPage";
import { LoginScreen, SessionProvider, useSession } from "./session";

export function App() {
  return (
    <SessionProvider>
      <Gate />
    </SessionProvider>
  );
}

function Gate() {
  const { ready, user, token } = useSession();
  if (!ready) return null;
  if (!user) return <LoginScreen />;
  return (
    <LiveProvider token={token}>
      <Shell />
    </LiveProvider>
  );
}

function Shell() {
  const route = useHashRoute();
  const { user, logout } = useSession();
  const { connected } = useLive();
  const [theme, setTheme] = useStoredState("mvt.theme", "auto");
  const alarmsV = useLiveVersion("alarms");
  const twinV = useLiveVersion("twin");
  const activeAlarms = useResource(() => api.alarms({ status: "active" }), [alarmsV]);
  const pendingRecs = useResource(() => api.recommendations(), [twinV]);

  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const page = route[0] ?? "";
  const nav = [
    { id: "", label: "Planta" },
    { id: "alarmas", label: "Alarmas", count: activeAlarms.data?.length, hot: activeAlarms.data?.some((a) => a.severity === "critical") },
    { id: "recomendaciones", label: "Mantenimiento", count: pendingRecs.data?.length, hot: pendingRecs.data?.some((r) => r.priority === "urgent") },
    { id: "eventos", label: "Visión" },
  ];
  const current = page === "activo" ? "" : page;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-name">
            Mine<span>Vision</span> Twin
          </div>
          <div className="brand-site">Mina demo · Concentradora</div>
        </div>
        <nav className="nav" aria-label="Secciones">
          {nav.map((n) => (
            <a key={n.id} href={href(n.id)} aria-current={current === n.id ? "page" : undefined}>
              {n.label}
              {n.count ? <span className={n.hot ? "count hot" : "count"}>{n.count}</span> : null}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className={connected ? "live-dot on" : "live-dot"}>{connected ? "En vivo" : "Reconectando…"}</span>
          <label className="field">
            Tema
            <select className="input" value={theme} onChange={(e) => setTheme(e.target.value)}>
              <option value="auto">Automático</option>
              <option value="dark">Sala de control (oscuro)</option>
              <option value="light">Claro</option>
            </select>
          </label>
          <div>
            <div className="secondary">{user?.name}</div>
            <div style={{ fontSize: 11 }}>{user?.roles.join(" · ")}</div>
            <button className="btn ghost" style={{ marginTop: 6 }} onClick={logout}>
              Salir
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        {page === "activo" && route[1] ? (
          <AssetPage key={route[1]} code={route[1]} />
        ) : page === "alarmas" ? (
          <AlarmsPage />
        ) : page === "recomendaciones" ? (
          <RecommendationsPage />
        ) : page === "eventos" ? (
          <EventsPage />
        ) : (
          <Overview />
        )}
      </main>
      <Toasts />
    </div>
  );
}

/** Avisos breves de lo que llega en vivo (alarmas nuevas, detecciones, recomendaciones). */
function Toasts() {
  const { last } = useLive();
  const [items, setItems] = useState<{ id: number; text: string; link?: string }[]>([]);

  useEffect(() => {
    if (!last) return;
    const text = describe(last);
    if (!text) return;
    const id = last.at + Math.random();
    setItems((xs) => [...xs.slice(-3), { id, ...text }]);
    const timer = window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 8000);
    return () => clearTimeout(timer);
  }, [last]);

  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className="toast">
          {t.link ? <a href={t.link}>{t.text}</a> : t.text}
        </div>
      ))}
    </div>
  );
}

function describe(msg: LiveMessage): { text: string; link?: string } | null {
  const d = msg.data;
  if (msg.channel === "alarms" && (d.event === "raised" || d.event === "escalated")) {
    return { text: `${d.event === "raised" ? "Nueva alarma" : "Alarma escalada"}: ${d.alarm.message}`, link: href("alarmas") };
  }
  if (msg.channel === "events") {
    return { text: `${eventLabel(d.type)} · ${d.message}`, link: href("activo", d.asset_code) };
  }
  if (msg.channel === "twin" && d.type === "recommendation" && d.recommendation.created_at === d.recommendation.updated_at) {
    return { text: `Nueva recomendación: ${d.recommendation.action}`, link: href("recomendaciones") };
  }
  return null;
}
