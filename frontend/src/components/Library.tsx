import { useMemo, useRef, useState } from "react";
import type { OrderShape, OrderStatus, TrackSummary } from "../types";
import { AudiusPanel, type SourceStatus } from "./AudiusPanel";
import { fmtTime } from "./DeckView";
import { OrderStrip } from "./OrderStrip";

interface Props {
  tracks: TrackSummary[];
  playingIds: number[];
  refCamelot: string | null;
  refBpm: number | null;
  scan: { running: boolean; done: number; total: number; current: string } | null;
  musicDir: string;
  onScan: (force: boolean) => void;
  onLoad: (trackId: number) => void;
  onAddFiles: (files: File[]) => void;
  onOpenFolder: () => void;
  upload: number | null; // 0..1 while uploading
  broken: { title: string; error: string }[];
  sourceStatus: SourceStatus;
  onError: (msg: string) => void;
  onTab: (tab: "mine" | "audius") => void;
  onStation: (query: string | null, genre: string | null) => void;
  order: OrderStatus | null;
  onOrder: (active: boolean, shape?: OrderShape) => void;
}

type SortKey = "artist" | "title" | "bpm" | "camelot" | "energy" | "duration" | "genre";

function camelotDist(a: string, b: string): number {
  const pa = [parseInt(a), a.slice(-1)] as const;
  const pb = [parseInt(b), b.slice(-1)] as const;
  let d = Math.abs(pa[0] - pb[0]);
  d = Math.min(d, 12 - d);
  return d + (pa[1] === pb[1] ? 0 : 1);
}

export function Library(p: Props) {
  const { tracks, playingIds, refCamelot, refBpm, scan, musicDir, onScan, onLoad } = p;
  const [q, setQ] = useState("");
  const [dropping, setDropping] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "artist", dir: 1 });
  const [matchOnly, setMatchOnly] = useState(false);
  const [tab, setTab] = useState<"mine" | "audius">("mine");
  const ordered = !!p.order?.active;
  const planPos = useMemo(() => new Map((p.order?.active ? p.order.items : []).map((i) => [i.id, i])), [p.order]);
  const byId = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let list = tracks.filter((t) =>
      !needle || `${t.artist} ${t.title} ${t.genre} ${t.camelot} ${Math.round(t.bpm)}`.toLowerCase().includes(needle));
    if (matchOnly && refCamelot && refBpm) {
      list = list.filter((t) => camelotDist(refCamelot, t.camelot) <= 1 && Math.abs(t.bpm / refBpm - 1) <= 0.08);
    }
    if (ordered) {
      // SMART ORDER: the table is the queue; tracks outside the plan go last.
      const pos = (t: TrackSummary) => planPos.get(t.id)?.pos ?? 1e6;
      return [...list].sort((a, b) => pos(a) - pos(b));
    }
    return [...list].sort((a, b) => {
      const va = a[sort.key] ?? "";
      const vb = b[sort.key] ?? "";
      if (sort.key === "camelot") return (parseInt(va as string) - parseInt(vb as string) || String(va).localeCompare(String(vb))) * sort.dir;
      return (typeof va === "number" ? (va as number) - (vb as number) : String(va).localeCompare(String(vb))) * sort.dir;
    });
  }, [tracks, q, sort, matchOnly, refCamelot, refBpm, ordered, planPos]);

  const th = (key: SortKey, label: string, cls = "") => (
    <th className={cls} onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : 1 }))}>
      {label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
    </th>
  );

  return (
    <section
      className={`library panel ${dropping ? "file-drop" : ""}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDropping(true);
        }
      }}
      onDragLeave={() => setDropping(false)}
      onDrop={(e) => {
        setDropping(false);
        if (e.dataTransfer.files.length) {
          e.preventDefault();
          p.onAddFiles([...e.dataTransfer.files]);
        }
      }}
    >
      <input
        ref={picker} type="file" multiple hidden
        accept="audio/*,.mp3,.wav,.flac,.aiff,.aif,.m4a,.aac,.ogg,.opus,.webm,.mp4"
        onChange={(e) => {
          if (e.target.files?.length) p.onAddFiles([...e.target.files]);
          e.target.value = "";
        }}
      />
      <header className="panel-head">
        <span className="tabs">
          <button className={`tab ${tab === "mine" ? "on" : ""}`} onClick={() => { setTab("mine"); p.onTab("mine"); }}
            title="AUTO mixes your own music">LIBRARY</button>
          <button className={`tab ${tab === "audius" ? "on" : ""}`} onClick={() => { setTab("audius"); p.onTab("audius"); }}
            title="AUTO mixes Audius tracks from your search (free, legal, artist-uploaded)">AUDIUS</button>
        </span>
        <button className={`btn small order-btn ${ordered ? "on" : ""}`} onClick={() => p.onOrder(!ordered)}
          title="Arrange the whole set: energy arc + smooth key/tempo neighbours. AUTO plays it in order">
          {ordered ? "● SMART ORDER" : "SMART ORDER"}
        </button>
        {tab === "audius" ? <span className="dim">AUTO mixes from this search - pick a genre or search an artist</span> : <>
        <span className="mono dim">{rows.length}/{tracks.length}</span>
        <input className="search" placeholder="search artist, title, key, bpm..." value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="chk" title="Only tracks that mix harmonically with the live deck">
          <input type="checkbox" checked={matchOnly} onChange={(e) => setMatchOnly(e.target.checked)} /> MATCH
        </label>
        <button className="btn small go" onClick={() => picker.current?.click()} disabled={p.upload !== null}
          title="Add music files (or drag them onto the library)">
          {p.upload !== null ? `ADDING ${Math.round(p.upload * 100)}%` : "+ ADD MUSIC"}
        </button>
        <button className="btn small" onClick={p.onOpenFolder} title={`Open ${musicDir}`}>FOLDER</button>
        <button className="btn small" disabled={scan?.running} onClick={(e) => onScan(e.shiftKey)}
          title="New files are picked up automatically. Shift-click to re-analyse everything">
          {scan?.running ? `SCANNING ${scan.done}/${scan.total}` : "SCAN"}
        </button>
        {p.broken.length > 0 && (
          <span className="broken" title={p.broken.map((b) => `${b.title}: ${b.error}`).join("\n")}>
            {p.broken.length} UNREADABLE
          </span>
        )}
        </>}
      </header>
      {ordered && p.order && (
        <OrderStrip order={p.order} tracks={byId} playingIds={playingIds} onShape={(s) => p.onOrder(true, s)} />
      )}
      {tab === "audius" && <AudiusPanel status={p.sourceStatus} onLoad={onLoad} onError={p.onError} onStation={p.onStation} refCamelot={refCamelot} />}
      {tab === "mine" && scan?.running && (
        <div className="scanbar"><div style={{ width: `${scan.total ? (scan.done / scan.total) * 100 : 0}%` }} /><span>{scan.current}</span></div>
      )}
      {tab === "mine" && <div className="table-wrap">
        {tracks.length === 0 ? (
          <div className="empty">
            <p>No tracks analysed yet.</p>
            <p className="dim">MUSIC_DIR = <span className="mono">{musicDir || "?"}</span></p>
            <p className="dim">Press + ADD MUSIC, drag files here, or copy them into that folder. New files are picked up automatically.</p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>{ordered && <th className="num">#</th>}{th("artist", "ARTIST")}{th("title", "TITLE")}{th("bpm", "BPM", "num")}{th("camelot", "KEY", "num")}{th("energy", "ENERGY", "num")}{th("duration", "TIME", "num")}{th("genre", "GENRE")}</tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const kd = refCamelot ? camelotDist(refCamelot, t.camelot) : null;
                const op = planPos.get(t.id);
                return (
                  <tr
                    key={t.id}
                    draggable
                    className={`${playingIds.includes(t.id) ? "playing" : ""} ${op?.played && !playingIds.includes(t.id) ? "done" : ""}`}
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/jevdj-track", String(t.id));
                      e.dataTransfer.effectAllowed = "copy";
                    }}
                    onDoubleClick={() => onLoad(t.id)}
                    title="Drag to a deck, or double-click to load the idle deck"
                  >
                    {ordered && (
                      <td className="num mono dim">
                        {op ? <>{op.pos}<span className={`sdot s${op.smooth ?? "x"}`} /></> : "-"}
                      </td>
                    )}
                    <td>{t.artist}</td>
                    <td className="t-title">
                      {t.title}
                      {t.source === "audius" && (
                        <a className="src-badge" href={t.permalink ?? "https://audius.co"} target="_blank" rel="noreferrer"
                          onClick={(e) => e.stopPropagation()} title="From Audius: open the artist's page">AUDIUS</a>
                      )}
                    </td>
                    <td className="num mono">{t.bpm.toFixed(1)}</td>
                    <td className={`num mono key ${kd === null ? "" : kd === 0 ? "k0" : kd === 1 ? "k1" : kd === 2 ? "k2" : ""}`}>{t.camelot}</td>
                    <td className="num"><span className="ebar"><i style={{ width: `${t.energy * 100}%` }} /></span></td>
                    <td className="num mono dim">{fmtTime(t.duration).slice(0, -2)}</td>
                    <td className="dim">{t.genre}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>}
    </section>
  );
}
