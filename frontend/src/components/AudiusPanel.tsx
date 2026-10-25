import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AudiusTrack } from "../api";
import { fmtTime } from "./DeckView";

const QUICK = ["amapiano", "afro house", "deep house", "gqom", "3 step", "private school piano", "afrobeats", "hip hop"];

export type SourceStatus = Record<string, { stage: string; error?: string; track_id?: number; title?: string }>;

interface Props {
  status: SourceStatus;
  onLoad: (trackId: number) => void;
  onMixIn: (trackId: number) => void;
  onError: (msg: string) => void;
  /** Called whenever the search changes: AUTO mixes from this station while the tab is open. */
  onStation: (query: string | null, genre: string | null) => void;
  refCamelot: string | null; // live track's key, to colour harmonic matches like the library
  refBpm: number | null;
}

function camelotDist(a: string, b: string): number {
  let d = Math.abs(parseInt(a) - parseInt(b));
  d = Math.min(d, 12 - d);
  return d + (a.slice(-1) === b.slice(-1) ? 0 : 1);
}

function bpmGap(a: number, b: number): number {
  let best = Infinity;
  for (const mult of [0.5, 1, 2]) {
    const g = Math.abs(a / (b * mult) - 1);
    if (g < best) best = g;
  }
  return best;
}

function harmonicFit(t: AudiusTrack, refCamelot: string | null, refBpm: number | null): { score: number; label: string; cls: string } {
  if (!t.added && !t.bpm && !t.camelot) return { score: -1, label: "", cls: "" };
  if (!refCamelot || !refBpm || !t.bpm || !t.camelot) {
    return t.added ? { score: 0.5, label: "READY", cls: "fit-ready" } : { score: 0, label: "", cls: "" };
  }
  const kd = camelotDist(refCamelot, t.camelot);
  const bg = bpmGap(refBpm, t.bpm);
  const keyScore = kd === 0 ? 1 : kd === 1 ? 0.85 : kd === 2 ? 0.45 : 0.1;
  const bpmScore = Math.max(0, 1 - bg / 0.16);
  const score = 0.55 * keyScore + 0.45 * bpmScore + (t.added ? 0.25 : 0);
  if (kd <= 1 && bg <= 0.04) return { score: score + 0.2, label: "PERFECT", cls: "fit-perfect" };
  if (kd <= 1 && bg <= 0.08) return { score: score + 0.1, label: "HARMONIC", cls: "fit-harmonic" };
  if (bg <= 0.08) return { score, label: "BPM SYNC", cls: "fit-sync" };
  return t.added ? { score, label: "ANALYSED", cls: "fit-ready" } : { score, label: "", cls: "" };
}

/** Search Audius (free, legal, artist-uploaded music) and add tracks to the library. */
export function AudiusPanel({ status, onLoad, onMixIn, onError, onStation, refCamelot, refBpm }: Props) {
  const [q, setQ] = useState("amapiano");
  const [results, setResults] = useState<AudiusTrack[]>([]);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"search" | "trending">("search");
  const [smartSort, setSmartSort] = useState(true);
  const pendingMixId = useRef<string | null>(null);

  /** station=false is a silent refresh (e.g. after the station prepared another track). */
  const run = async (query: string, m: "search" | "trending" = "search", station = true) => {
    if (station) setBusy(true);
    setMode(m);
    if (station) onStation(m === "trending" ? null : query, m === "trending" ? "Electronic" : null);
    try {
      setResults(m === "trending" ? await api.audiusTrending("Electronic") : await api.audiusSearch(query));
    } catch (e) {
      if (station) onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void run("amapiano");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When a track finishes adding, refresh flags so the row flips to "MIX ▶ / LOAD".
  const doneCount = Object.values(status).filter((s) => s.stage === "done").length;
  useEffect(() => {
    if (doneCount && results.length) void (mode === "trending" ? run("", "trending", false) : run(q, "search", false));
    if (pendingMixId.current) {
      const st = status[pendingMixId.current];
      if (st?.stage === "done" && st.track_id) {
        const tid = st.track_id;
        pendingMixId.current = null;
        onMixIn(tid);
      } else if (st?.stage === "error") {
        pendingMixId.current = null;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneCount, status]);

  const pullAndMix = async (t: AudiusTrack) => {
    if (t.added && t.track_id) {
      onMixIn(t.track_id);
      return;
    }
    pendingMixId.current = t.id;
    try {
      await api.audiusAdd(t.id);
    } catch (e) {
      pendingMixId.current = null;
      onError((e as Error).message);
    }
  };

  const sortedResults = useMemo(() => {
    if (!smartSort) return results;
    return [...results].sort((a, b) => {
      const fa = harmonicFit(a, refCamelot, refBpm).score;
      const fb = harmonicFit(b, refCamelot, refBpm).score;
      if (Math.abs(fb - fa) > 0.01) return fb - fa;
      return 0;
    });
  }, [results, smartSort, refCamelot, refBpm]);

  return (
    <div className="audius">
      <div className="audius-bar">
        <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) void run(q.trim()); }}>
          <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="search Audius: artist, track, genre..." />
        </form>
        <button className="btn small" onClick={() => void run("", "trending")}>TRENDING</button>
        <button
          className={`btn small ${smartSort ? "on" : ""}`}
          onClick={() => setSmartSort((v) => !v)}
          title="Smart Queue: rank tracks by BPM & Camelot key compatibility with the live deck"
        >
          {smartSort ? "● SMART QUEUE" : "SMART QUEUE"}
        </button>
        <a className="powered" href="https://audius.co" target="_blank" rel="noreferrer">via Audius</a>
      </div>
      <div className="chips">
        {QUICK.map((g) => (
          <button key={g} className={`chip ${q === g && mode === "search" ? "on" : ""}`} onClick={() => { setQ(g); void run(g); }}>{g}</button>
        ))}
      </div>
      <div className="table-wrap">
        {busy ? <div className="empty dim">Searching Audius...</div> : sortedResults.length === 0 ? (
          <div className="empty dim">No full-length streamable tracks found. Try another search.</div>
        ) : (
          <table>
            <thead>
              <tr><th>ARTIST</th><th>TITLE</th><th className="num">FIT</th><th className="num">BPM</th><th className="num">KEY</th><th className="num">ENERGY</th><th className="num">TIME</th><th className="num">PLAYS</th><th /></tr>
            </thead>
            <tbody>
              {sortedResults.map((t) => {
                const st = status[t.id];
                const adding = t.adding || (st && st.stage !== "done" && st.stage !== "error");
                const fit = harmonicFit(t, refCamelot, refBpm);
                const isPendingMix = pendingMixId.current === t.id;
                return (
                  <tr key={t.id} onDoubleClick={() => void pullAndMix(t)} title="Double-click to analyse BPM/key and smoothly mix in">
                    <td className="ellipsis">{t.artist}</td>
                    <td className="t-title ellipsis">
                      {t.permalink ? <a href={t.permalink} target="_blank" rel="noreferrer">{t.title}</a> : t.title}
                    </td>
                    <td className="num">
                      {fit.label ? <span className={`fit-badge ${fit.cls}`}>{fit.label}</span> : <span className="dim">-</span>}
                    </td>
                    <td className="num mono">{t.bpm ? t.bpm.toFixed(0) : "-"}</td>
                    {(() => {
                      const kd = refCamelot && t.camelot ? camelotDist(refCamelot, t.camelot) : null;
                      const cls = kd === null ? "" : kd === 0 ? "k0" : kd === 1 ? "k1" : kd === 2 ? "k2" : "";
                      return <td className={`num mono key ${cls}`} title={t.key ?? ""}>{t.camelot ?? t.key ?? "-"}</td>;
                    })()}
                    <td className="num" title={t.energy == null ? "Energy is measured when the track is added" : `energy ${t.energy.toFixed(2)}`}>
                      <span className={`ebar ${t.energy == null ? "unknown" : ""}`}><i style={{ width: `${(t.energy ?? 0) * 100}%` }} /></span>
                    </td>
                    <td className="num mono dim">{fmtTime(t.duration).slice(0, -2)}</td>
                    <td className="num mono dim">{t.plays >= 1000 ? `${(t.plays / 1000).toFixed(1)}k` : t.plays}</td>
                    <td className="num row-actions">
                      {t.added && t.track_id ? (
                        <>
                          <button className="btn tiny go" onClick={() => onMixIn(t.track_id!)} title="Smoothly mix this track in right now">MIX ▶</button>
                          <button className="btn tiny" onClick={() => onLoad(t.track_id!)} title="In your library: load to the idle deck">LOAD</button>
                        </>
                      ) : st?.stage === "error" ? (
                        <button className="btn tiny warn" title={st.error} onClick={() => void api.audiusAdd(t.id)}>RETRY</button>
                      ) : adding ? (
                        <span className="mono adding">{isPendingMix ? "MIXING SOON" : st?.stage === "analysing" ? "ANALYSING" : "FETCHING"}</span>
                      ) : (
                        <>
                          <button className="btn tiny go" onClick={() => void pullAndMix(t)} title="Fetch, analyse BPM & key, and smoothly mix in">MIX ▶</button>
                          <button className="btn tiny" onClick={() => void api.audiusAdd(t.id).catch((e) => onError(e.message))}>+ ADD</button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
