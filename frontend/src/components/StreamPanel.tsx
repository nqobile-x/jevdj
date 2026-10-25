import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AudiusTrack } from "../api";
import type { SourceStatus } from "./AudiusPanel";
import { fmtTime } from "./DeckView";

const QUICK = [
  "kabza de small amapiano",
  "kelvin momo private school piano",
  "uncle waffles amapiano",
  "drake r&b",
  "south african afro house",
  "3 step afro tech",
  "tyla amapiano",
  "tiktok amapiano viral",
];

interface Props {
  status: SourceStatus;
  onLoad: (trackId: number) => void;
  onMixIn: (trackId: number) => void;
  onError: (msg: string) => void;
  /** Called whenever the search changes: AUTO mixes from this station while the tab is open. */
  onStation: (query: string | null, genre: string | null, source: "audius" | "stream") => void;
  refCamelot: string | null;
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
  if (!t.added || !t.bpm || !t.camelot) return { score: t.added ? 0.2 : -1, label: "", cls: "" };
  if (!refCamelot || !refBpm) return { score: 0.5, label: "READY", cls: "fit-ready" };
  const kd = camelotDist(refCamelot, t.camelot);
  const bg = bpmGap(refBpm, t.bpm);
  const keyScore = kd === 0 ? 1 : kd === 1 ? 0.85 : kd === 2 ? 0.45 : 0.1;
  const bpmScore = Math.max(0, 1 - bg / 0.16);
  const score = 0.55 * keyScore + 0.45 * bpmScore;
  if (kd <= 1 && bg <= 0.04) return { score: score + 0.2, label: "PERFECT", cls: "fit-perfect" };
  if (kd <= 1 && bg <= 0.08) return { score: score + 0.1, label: "HARMONIC", cls: "fit-harmonic" };
  if (bg <= 0.08) return { score, label: "BPM SYNC", cls: "fit-sync" };
  return { score, label: "ANALYSED", cls: "fit-ready" };
}

function isUrl(s: string): boolean {
  const t = s.trim();
  return t.startsWith("http://") || t.startsWith("https://");
}

/** Search YouTube Music / YouTube or paste a YouTube / TikTok link directly. */
export function StreamPanel({ status, onLoad, onMixIn, onError, onStation, refCamelot, refBpm }: Props) {
  const [q, setQ] = useState("kabza de small amapiano");
  const [activeQuery, setActiveQuery] = useState("kabza de small amapiano");
  const [urlInput, setUrlInput] = useState("");
  const [results, setResults] = useState<AudiusTrack[]>([]);
  const [busy, setBusy] = useState(false);
  const [addingUrl, setAddingUrl] = useState(false);
  const [smartSort, setSmartSort] = useState(true);
  const pendingMixId = useRef<string | null>(null);

  const run = async (query: string, station = true) => {
    const clean = query.trim();
    if (!clean) return;
    setActiveQuery(clean);
    if (station) setBusy(true);
    if (station && !isUrl(clean)) onStation(clean, null, "stream");
    try {
      setResults(await api.streamSearch(clean));
    } catch (e) {
      if (station) onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void run("kabza de small amapiano");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doneCount = Object.values(status).filter((s) => s.stage === "done").length;
  useEffect(() => {
    if (doneCount && results.length && activeQuery.trim()) void run(activeQuery, false);
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

  const submitUrl = async (raw: string) => {
    const u = raw.trim();
    if (!u) return;
    if (!isUrl(u)) {
      setQ(u);
      void run(u);
      return;
    }
    setAddingUrl(true);
    try {
      const res = await api.streamAdd("", u);
      setUrlInput("");
      if (res.track_id) {
        onMixIn(res.track_id);
      } else if (res.id) {
        pendingMixId.current = res.id;
        void run(u, false);
      }
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setAddingUrl(false);
    }
  };

  const pullAndMix = async (t: AudiusTrack) => {
    if (t.added && t.track_id) {
      onMixIn(t.track_id);
      return;
    }
    pendingMixId.current = t.id;
    try {
      const res = await api.streamAdd(t.id, t.permalink);
      if (res.track_id) {
        pendingMixId.current = null;
        onMixIn(res.track_id);
      }
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
    <div className="audius stream-panel">
      <div className="audius-bar">
        <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) void run(q.trim()); }}>
          <input
            className="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search YouTube Music: amapiano, Kelvin Momo, Drake, afro house..."
          />
        </form>
        <form
          className="url-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submitUrl(urlInput);
          }}
        >
          <input
            className="search url-input"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="or paste YouTube / YT Music / TikTok URL to smooth-mix..."
          />
          <button className="btn small go" type="submit" disabled={addingUrl || !urlInput.trim()}>
            {addingUrl ? "PULLING..." : "+ PULL & MIX"}
          </button>
        </form>
        <button
          className={`btn small ${smartSort ? "on" : ""}`}
          onClick={() => setSmartSort((v) => !v)}
          title="Smart Queue: rank analysed tracks by BPM & Camelot key compatibility with the live deck"
        >
          {smartSort ? "● SMART QUEUE" : "SMART QUEUE"}
        </button>
      </div>
      <div className="chips">
        {QUICK.map((g) => (
          <button
            key={g}
            className={`chip ${q === g ? "on" : ""}`}
            onClick={() => {
              setQ(g);
              void run(g);
            }}
          >
            {g}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        {busy ? (
          <div className="empty dim">Searching YouTube Music...</div>
        ) : sortedResults.length === 0 ? (
          <div className="empty dim">No tracks found. Search an artist/track or paste a YouTube or TikTok URL above.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ARTIST</th>
                <th>TITLE</th>
                <th className="num">FIT</th>
                <th className="num">BPM</th>
                <th className="num">KEY</th>
                <th className="num">ENERGY</th>
                <th className="num">TIME</th>
                <th className="num">VIEWS</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sortedResults.map((t) => {
                const st = status[t.id];
                const adding = t.adding || (st && st.stage !== "done" && st.stage !== "error");
                const fit = harmonicFit(t, refCamelot, refBpm);
                const isPendingMix = pendingMixId.current === t.id;
                return (
                  <tr
                    key={t.id}
                    draggable={!!(t.added && t.track_id)}
                    onDragStart={(e) => {
                      if (t.track_id) {
                        e.dataTransfer.setData("text/jevdj-track", String(t.track_id));
                        e.dataTransfer.effectAllowed = "copy";
                      }
                    }}
                    onDoubleClick={() => void pullAndMix(t)}
                    title="Double-click to analyse BPM/key and smoothly mix in"
                  >
                    <td className="ellipsis">{t.artist}</td>
                    <td className="t-title ellipsis">
                      {t.permalink ? (
                        <a href={t.permalink} target="_blank" rel="noreferrer">
                          {t.title}
                        </a>
                      ) : (
                        t.title
                      )}
                      <span className={`src-badge ${t.platform === "tiktok" ? "tiktok" : "youtube"}`}>
                        {t.platform === "tiktok" ? "TIKTOK" : "YT MUSIC"}
                      </span>
                    </td>
                    <td className="num">
                      {fit.label ? <span className={`fit-badge ${fit.cls}`}>{fit.label}</span> : <span className="dim">-</span>}
                    </td>
                    <td className="num mono">{t.bpm ? t.bpm.toFixed(0) : "-"}</td>
                    {(() => {
                      const kd = refCamelot && t.camelot ? camelotDist(refCamelot, t.camelot) : null;
                      const cls = kd === null ? "" : kd === 0 ? "k0" : kd === 1 ? "k1" : kd === 2 ? "k2" : "";
                      return (
                        <td className={`num mono key ${cls}`} title={t.key ?? ""}>
                          {t.camelot ?? t.key ?? "-"}
                        </td>
                      );
                    })()}
                    <td
                      className="num"
                      title={t.energy == null ? "Energy is measured when the track is added" : `energy ${t.energy.toFixed(2)}`}
                    >
                      <span className={`ebar ${t.energy == null ? "unknown" : ""}`}>
                        <i style={{ width: `${(t.energy ?? 0) * 100}%` }} />
                      </span>
                    </td>
                    <td className="num mono dim">{t.duration ? fmtTime(t.duration).slice(0, -2) : "-"}</td>
                    <td className="num mono dim">
                      {t.plays >= 1_000_000
                        ? `${(t.plays / 1_000_000).toFixed(1)}M`
                        : t.plays >= 1000
                        ? `${(t.plays / 1000).toFixed(1)}k`
                        : t.plays || "-"}
                    </td>
                    <td className="num row-actions">
                      {t.added && t.track_id ? (
                        <>
                          <button
                            className="btn tiny go"
                            onClick={() => onMixIn(t.track_id!)}
                            title="Smoothly mix this track in right now using Jev's BPM/key transition brain"
                          >
                            MIX ▶
                          </button>
                          <button className="btn tiny" onClick={() => onLoad(t.track_id!)} title="Queue next / load onto the idle deck">
                            LOAD
                          </button>
                        </>
                      ) : st?.stage === "error" ? (
                        <button
                          className="btn tiny warn"
                          title={st.error}
                          onClick={() => void api.streamAdd(t.id, t.permalink).catch((e) => onError(e.message))}
                        >
                          RETRY
                        </button>
                      ) : adding ? (
                        <span className="mono adding">
                          {isPendingMix ? "MIXING SOON" : st?.stage === "analysing" ? "ANALYSING" : "PULLING"}
                        </span>
                      ) : (
                        <>
                          <button
                            className="btn tiny go"
                            onClick={() => void pullAndMix(t)}
                            title="Pull, analyse BPM & key, and smoothly mix in as soon as ready"
                          >
                            MIX ▶
                          </button>
                          <button
                            className="btn tiny"
                            onClick={() => void api.streamAdd(t.id, t.permalink).catch((e) => onError(e.message))}
                            title="Pull and analyse BPM/key into station crate"
                          >
                            + ADD
                          </button>
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
