import { useEffect, useState } from "react";
import { api, type AudiusTrack } from "../api";
import { fmtTime } from "./DeckView";

const QUICK = ["amapiano", "afro house", "deep house", "gqom", "3 step", "private school piano", "afrobeats", "hip hop"];

export type SourceStatus = Record<string, { stage: string; error?: string }>;

interface Props {
  status: SourceStatus;
  onLoad: (trackId: number) => void;
  onError: (msg: string) => void;
  /** Called whenever the search changes: AUTO mixes from this station while the tab is open. */
  onStation: (query: string | null, genre: string | null) => void;
  refCamelot: string | null; // live track's key, to colour harmonic matches like the library
}

function camelotDist(a: string, b: string): number {
  let d = Math.abs(parseInt(a) - parseInt(b));
  d = Math.min(d, 12 - d);
  return d + (a.slice(-1) === b.slice(-1) ? 0 : 1);
}

/** Search Audius (free, legal, artist-uploaded music) and add tracks to the library. */
export function AudiusPanel({ status, onLoad, onError, onStation, refCamelot }: Props) {
  const [q, setQ] = useState("amapiano");
  const [results, setResults] = useState<AudiusTrack[]>([]);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"search" | "trending">("search");

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

  // When a track finishes adding, refresh flags so the row flips to "LOAD".
  const doneCount = Object.values(status).filter((s) => s.stage === "done").length;
  useEffect(() => {
    if (doneCount && results.length) void (mode === "trending" ? run("", "trending", false) : run(q, "search", false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneCount]);

  return (
    <div className="audius">
      <div className="audius-bar">
        <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) void run(q.trim()); }}>
          <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="search Audius: artist, track, genre..." />
        </form>
        <button className="btn small" onClick={() => void run("", "trending")}>TRENDING</button>
        <a className="powered" href="https://audius.co" target="_blank" rel="noreferrer">via Audius</a>
      </div>
      <div className="chips">
        {QUICK.map((g) => (
          <button key={g} className={`chip ${q === g && mode === "search" ? "on" : ""}`} onClick={() => { setQ(g); void run(g); }}>{g}</button>
        ))}
      </div>
      <div className="table-wrap">
        {busy ? <div className="empty dim">Searching Audius...</div> : results.length === 0 ? (
          <div className="empty dim">No full-length streamable tracks found. Try another search.</div>
        ) : (
          <table>
            <thead>
              <tr><th>ARTIST</th><th>TITLE</th><th className="num">BPM</th><th className="num">KEY</th><th className="num">ENERGY</th><th className="num">TIME</th><th className="num">PLAYS</th><th /></tr>
            </thead>
            <tbody>
              {results.map((t) => {
                const st = status[t.id];
                const adding = t.adding || (st && st.stage !== "done" && st.stage !== "error");
                return (
                  <tr key={t.id}>
                    <td className="ellipsis">{t.artist}</td>
                    <td className="t-title ellipsis">
                      {t.permalink ? <a href={t.permalink} target="_blank" rel="noreferrer">{t.title}</a> : t.title}
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
                    <td className="num">
                      {t.added && t.track_id ? (
                        <button className="btn tiny" onClick={() => onLoad(t.track_id!)} title="In your library: load to the idle deck">LOAD</button>
                      ) : st?.stage === "error" ? (
                        <button className="btn tiny warn" title={st.error} onClick={() => void api.audiusAdd(t.id)}>RETRY</button>
                      ) : adding ? (
                        <span className="mono adding">{st?.stage === "analysing" ? "ANALYSING" : "FETCHING"}</span>
                      ) : (
                        <button className="btn tiny go" onClick={() => void api.audiusAdd(t.id).catch((e) => onError(e.message))}>+ ADD</button>
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
