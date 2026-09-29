// Actualizaciones en vivo: una conexión WebSocket por canal (alarms, events, twin).
// Cada mensaje incrementa la "versión" del canal; las vistas que dependen de él se recargan.

import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type Channel = "alarms" | "events" | "twin";

export interface LiveMessage {
  channel: Channel;
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  at: number;
}

interface LiveState {
  versions: Record<Channel, number>;
  connected: boolean;
  last: LiveMessage | null;
}

const LiveContext = createContext<LiveState>({
  versions: { alarms: 0, events: 0, twin: 0 },
  connected: false,
  last: null,
});

const CHANNELS: Channel[] = ["alarms", "events", "twin"];

export function wsUrl(path: string, token?: string | null): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = token ? `?access_token=${encodeURIComponent(token)}` : "";
  return `${proto}//${window.location.host}${path}${qs}`;
}

export function LiveProvider({ children, token }: { children: ReactNode; token: string | null }) {
  const [versions, setVersions] = useState<Record<Channel, number>>({ alarms: 0, events: 0, twin: 0 });
  const [open, setOpen] = useState<Record<Channel, boolean>>({ alarms: false, events: false, twin: false });
  const [last, setLast] = useState<LiveMessage | null>(null);
  const retries = useRef<Record<Channel, number>>({ alarms: 0, events: 0, twin: 0 });

  useEffect(() => {
    let disposed = false;
    const sockets: WebSocket[] = [];
    const timers: number[] = [];

    const connect = (channel: Channel) => {
      if (disposed) return;
      const ws = new WebSocket(wsUrl(`/ws/${channel}`, token));
      sockets.push(ws);
      ws.onopen = () => {
        retries.current[channel] = 0;
        setOpen((o) => ({ ...o, [channel]: true }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type !== "message") return;
        setVersions((v) => ({ ...v, [channel]: v[channel] + 1 }));
        setLast({ channel, data: msg.data, at: Date.now() });
      };
      ws.onclose = () => {
        setOpen((o) => ({ ...o, [channel]: false }));
        if (disposed) return;
        const attempt = ++retries.current[channel];
        timers.push(window.setTimeout(() => connect(channel), Math.min(15000, 1000 * 2 ** attempt)));
      };
    };

    CHANNELS.forEach(connect);
    return () => {
      disposed = true;
      timers.forEach(clearTimeout);
      sockets.forEach((s) => s.close());
    };
  }, [token]);

  const value = useMemo(
    () => ({ versions, connected: CHANNELS.every((c) => open[c]), last }),
    [versions, open, last],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveState {
  return useContext(LiveContext);
}

/** Versión combinada de los canales indicados: úsela como dependencia para recargar datos. */
export function useLiveVersion(...channels: Channel[]): number {
  const { versions } = useLive();
  return channels.reduce((acc, c) => acc + versions[c], 0);
}
