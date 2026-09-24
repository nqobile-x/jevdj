import { useEffect, useState } from "react";
import type { Deck } from "../audio/deck";
import { useStore } from "../store";
import { Waveform } from "./Waveform";

interface Props {
  deck: Deck;
  color: string;
  other: Deck;
  live: boolean;
  mixOut: number | null;
  mixIn: number | null;
  onDropTrack: (deck: Deck, trackId: number) => void;
  autoMatch: boolean;
}

export function fmtTime(s: number): string {
  if (!isFinite(s)) return "--:--";
  const sign = s < 0 ? "-" : "";
  s = Math.abs(s);
  return `${sign}${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, "0")}.${Math.floor((s * 10) % 10)}`;
}

export function DeckView({ deck, color, other, live, mixOut, mixIn, onDropTrack, autoMatch }: Props) {
  const st = useStore(deck.store);
  const [pos, setPos] = useState(0);
  const [over, setOver] = useState(false);

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      setPos(deck.position());
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [deck]);

  const t = st.track;
  const bpm = t ? t.bpm * st.rate : 0;
  const pitch = (st.rate - 1) * 100;
  const remaining = st.duration - pos;
  const beat = t ? Math.floor((pos - t.first_downbeat) / (t.beat_period || 60 / t.bpm)) : 0;
  const beatInBar = ((beat % 4) + 4) % 4;
  const play = () => {
    // AUTO MATCH: starting while the other deck plays lands tempo- and bar-aligned.
    if (!st.playing && autoMatch && other.playing) deck.playSynced(other);
    else deck.toggle();
  };
  const nudgeMs = 10;

  return (
    <section
      className={`deck deck-${deck.id} ${live ? "is-live" : ""} ${over ? "drop-over" : ""}`}
      style={{ ["--deck" as string]: color }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("text/jevdj-track")) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        setOver(false);
        const id = Number(e.dataTransfer.getData("text/jevdj-track"));
        if (id) onDropTrack(deck, id);
      }}
    >
      <header className="deck-head">
        <div className="deck-letter">{deck.id}</div>
        <div className="deck-title">
          <div className="title">{t ? t.title : st.loading ? "Loading..." : "Empty deck"}</div>
          <div className="artist">{t ? t.artist || "Unknown artist" : st.error ?? "Drag a track from the library"}</div>
        </div>
        <div className="deck-stats">
          <div className="stat"><span className="lbl">KEY</span><span className="mono big">{t?.camelot ?? "--"}</span></div>
          <div className="stat"><span className="lbl">BPM</span><span className="mono big">{t ? bpm.toFixed(1) : "---.-"}</span></div>
        </div>
      </header>

      <Waveform deck={deck} color={color} zoomed mixOut={mixOut} mixIn={mixIn} />
      <Waveform deck={deck} color={color} mixOut={mixOut} mixIn={mixIn} />

      <div className="deck-info mono">
        <span>{fmtTime(pos)}</span>
        <span className="beats">
          {[0, 1, 2, 3].map((i) => <i key={i} className={st.playing && i === beatInBar ? "on" : ""} />)}
        </span>
        <span className="dim">-{fmtTime(Math.max(0, remaining))}</span>
        <span className={Math.abs(pitch) > 0.05 ? "pitch hot" : "pitch"}>{pitch >= 0 ? "+" : ""}{pitch.toFixed(2)}%</span>
        <span className="dim">E {t ? t.energy.toFixed(2) : "--"}</span>
      </div>

      <div className="deck-controls">
        <button className="btn cue" onClick={() => deck.cue()} disabled={!t}>CUE</button>
        <button className={`btn play ${st.playing ? "on" : ""}`} onClick={play} disabled={!t}>
          {st.playing ? "❚❚" : "▶"}
        </button>
        <button className="btn sync" onClick={() => deck.sync(other)} disabled={!t || !other.track}
          title="Match tempo, and beat phase if both decks are playing">SYNC</button>
        <div className="pitch-fader">
          <span className="lbl">PITCH</span>
          <input
            type="range" min={0.92} max={1.08} step={0.0005} value={st.rate}
            onChange={(e) => deck.setRate(Number(e.target.value))}
            onDoubleClick={() => deck.setRate(1)}
          />
        </div>
      </div>

      <div className="deck-tools">
        <span className="lbl">LOOP</span>
        {[1, 4, 8].map((n) => (
          <button key={n} className={`btn tiny ${st.loop && Math.abs((st.loop.end - st.loop.start) / (4 * deck.beatPeriod) - n) < 0.01 ? "on" : ""}`}
            disabled={!t} onClick={() => deck.loopBars(n)} title={`Loop ${n} bar${n > 1 ? "s" : ""} (press again to exit)`}>{n}</button>
        ))}
        <span className="sep" />
        <span className="lbl">GRID</span>
        <button className="btn tiny" disabled={!t} onClick={() => void deck.shiftGrid(-nudgeMs / 1000)} title="Move beat grid 10 ms earlier">◀</button>
        <button className="btn tiny" disabled={!t} onClick={() => void deck.shiftGrid(nudgeMs / 1000)} title="Move beat grid 10 ms later">▶</button>
        <button className="btn tiny" disabled={!t} onClick={() => void deck.shiftGrid(deck.beatPeriod / 2)}
          title="Grid is on the offbeat: shift half a beat">½</button>
        {st.loop && <span className="loop-tag mono">LOOP</span>}
      </div>
    </section>
  );
}
