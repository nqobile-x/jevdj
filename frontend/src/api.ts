import type {
  Decision, Flair, Health, LearnedStyle, MixStyle, NextResponse, RadioStatus, ServerEvent, TrackDetail, TrackSummary, TransitionPlan, Vibe, VoiceLine,
} from "./types";

export const API = "/api";

export interface AudiusTrack {
  id: string;
  title: string;
  artist: string;
  genre: string;
  duration: number;
  bpm: number | null;
  key: string | null;
  camelot: string | null;
  energy: number | null; // Jev's measurement: only once the track is added
  plays: number;
  artwork: string | null;
  permalink: string | null;
  added: boolean;
  adding: boolean;
  track_id: number | null;
}

async function req<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(API + path, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, json: unknown = {}) => req<T>(path, { method: "POST", json });

export const api = {
  health: () => req<Health>("/health"),
  library: () => req<TrackSummary[]>("/library"),
  track: (id: number) => req<TrackDetail>(`/library/${id}`),
  scan: (force = false) => post<{ started: boolean; files: number }>("/library/scan", { force }),
  audioUrl: (id: number) => `${API}/audio/${id}`,
  startSet: (vibe: Vibe) => post<{ set_id: number; vibe: Vibe }>("/set/start", { vibe }),
  setVibe: (vibe: Vibe) => post("/set/vibe", { vibe }),
  next: (body: { current_id: number | null; history: number[]; vibe: Vibe; exclude?: number[]; switch?: boolean }) =>
    post<NextResponse>("/brain/next", body),
  transition: (from_id: number, to_id: number, start_s: number | null = null) =>
    post<TransitionPlan>("/brain/transition", { from_id, to_id, start_s }),
  setMixStyle: (mix_style: MixStyle) => post("/set/mix-style", { mix_style }),
  grid: (id: number, shift_s: number, source: "user" | "beatlock") =>
    post<TrackDetail>(`/library/${id}/grid`, { shift_s, source }),
  openFolder: () => post<{ opened: string }>("/library/open-folder"),
  libraryErrors: () => req<TrackSummary[]>("/library?errors=true").then((l) => l.filter((t) => (t as TrackSummary & { error?: string }).error)),
  /** Upload files into MUSIC_DIR with progress (0..1). */
  upload: (files: File[], onProgress: (p: number) => void) =>
    new Promise<{ saved: string[]; rejected: { file: string; reason: string }[] }>((resolve, reject) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f, f.name));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API}/library/upload`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => (xhr.status < 300 ? resolve(JSON.parse(xhr.responseText)) : reject(new Error(`${xhr.status} ${xhr.responseText}`)));
      xhr.onerror = () => reject(new Error("upload failed"));
      xhr.send(form);
    }),
  override: (action: string, track_id: number | null, note?: string) =>
    post("/brain/override", { action, track_id, note }),
  played: (track_id: number, transition: string | null, source: string | null) =>
    post("/history/played", { track_id, transition, source }),
  decisions: (limit = 80) => req<Decision[]>(`/decisions?limit=${limit}`),
  voiceLine: (current_id: number | null, next_id: number, mode: "intro" | "next" | "switch" = "next") =>
    post<VoiceLine>("/voice/line", { current_id, next_id, mode }),
  exportUrl: (format: "json" | "txt") => `${API}/history/export?format=${format}`,
  audiusSearch: (q: string) => req<AudiusTrack[]>(`/sources/audius/search?q=${encodeURIComponent(q)}&limit=40`),
  audiusTrending: (genre: string) => req<AudiusTrack[]>(`/sources/audius/trending?genre=${encodeURIComponent(genre)}&limit=40`),
  audiusAdd: (id: string) => post<{ queued: boolean }>("/sources/audius/add", { id }),
  radioStart: (query: string | null, genre: string | null = null) => post<RadioStatus>("/radio/start", { query, genre }),
  radioStop: () => post<RadioStatus>("/radio/stop"),
  setFlair: (flair: Flair) => post("/set/flair", { flair }),
  feedback: (value: 1 | -1, style: string, note?: string) =>
    post<{ learned: Record<string, LearnedStyle> }>("/feedback", { kind: "transition", value, style, note }),
  learning: () => req<{ styles: Record<string, LearnedStyle> }>("/learning"),
  resetLearning: () => post("/learning/reset"),
  alternatives: (from_id: number, to_id: number, start_s: number | null, exclude: string[]) =>
    post<TransitionPlan[]>("/brain/alternatives", { from_id, to_id, start_s, exclude }),
  prelistenLog: (body: { score: number; style: string; issues: string[]; fixes: string[]; beat_error_ms: number | null;
    switched_from?: string | null; render_ms?: number }) => post("/brain/prelisten", body),
};

/** Reconnecting WebSocket for /events. */
export function connectEvents(onEvent: (e: ServerEvent) => void, onStatus: (up: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 500;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}${API}/events`);
    ws.onopen = () => {
      retry = 500;
      onStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      onStatus(false);
      if (!closed) setTimeout(open, (retry = Math.min(retry * 2, 8000)));
    };
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}
