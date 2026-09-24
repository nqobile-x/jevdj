import { api } from "../api";
import type { Flair, MixStyle, Phase, Vibe } from "../types";

const VIBES: Vibe[] = ["auto", "warm-up", "build", "peak", "cool-down"];
const MIX_STYLES: [MixStyle, string, string][] = [
  ["radio", "RADIO", "Play full tracks, mix at the outro"],
  ["club", "CLUB", "Play the best 2-3 minutes, leave after the peak"],
  ["quick", "QUICK", "1-1.5 minutes each, fast cuts and drops"],
];
const FLAIRS: [Flair, string, string][] = [
  ["smooth", "SMOOTH", "Classic blends and cuts only"],
  ["creative", "CREATIVE", "Adds chops, teases and the odd run-it-back, when they suit the music"],
  ["turnt", "TURNT", "Routines first: chops, teases, rewinds, edits"],
];

interface Props {
  auto: boolean;
  onAuto: () => void;
  vibe: Vibe;
  phase: Phase | null;
  onVibe: (v: Vibe) => void;
  voice: boolean;
  onVoice: () => void;
  masterBpm: number;
  jevOnline: boolean;
  wsUp: boolean;
  recording: boolean;
  recSeconds: number;
  recBytes: number;
  onRecord: () => void;
  sampleRate: number;
  mixStyle: MixStyle;
  onMixStyle: (m: MixStyle) => void;
  autoMatch: boolean;
  onAutoMatch: () => void;
  flair: Flair;
  onFlair: (f: Flair) => void;
}

export function TopBar(p: Props) {
  const mm = Math.floor(p.recSeconds / 60);
  const ss = Math.floor(p.recSeconds % 60).toString().padStart(2, "0");
  return (
    <header className="topbar">
      <div className="logo">
        JEV<span>DJ</span>
      </div>
      <button className={`btn toggle ${p.auto ? "on" : ""}`} onClick={p.onAuto}>
        AUTO: {p.auto ? "ON" : "OFF"}
      </button>
      <div className="seg" role="group" aria-label="Vibe">
        <span className="lbl">VIBE</span>
        {VIBES.map((v) => (
          <button key={v} className={`seg-btn ${p.vibe === v ? "on" : ""}`} onClick={() => p.onVibe(v)}>
            {v === "auto" && p.vibe === "auto" && p.phase ? `AUTO · ${p.phase}` : v}
          </button>
        ))}
      </div>
      <div className="seg" role="group" aria-label="Mix length">
        <span className="lbl">MIX</span>
        {MIX_STYLES.map(([m, label, tip]) => (
          <button key={m} className={`seg-btn ${p.mixStyle === m ? "on" : ""}`} title={tip} onClick={() => p.onMixStyle(m)}>{label}</button>
        ))}
      </div>
      <div className="seg" role="group" aria-label="Flair">
        <span className="lbl">FLAIR</span>
        {FLAIRS.map(([f, label, tip]) => (
          <button key={f} className={`seg-btn ${p.flair === f ? "on" : ""}`} title={tip} onClick={() => p.onFlair(f)}>{label}</button>
        ))}
      </div>
      <button className={`btn toggle ${p.autoMatch ? "on" : ""}`} onClick={p.onAutoMatch}
        title="AUTO MATCH: loaded tracks sync tempo, start on the beat, and the beat lock keeps them in phase">
        MATCH: {p.autoMatch ? "ON" : "OFF"}
      </button>
      <button className={`btn toggle ${p.voice ? "on" : ""}`} onClick={p.onVoice}>
        VOICE: {p.voice ? "ON" : "OFF"}
      </button>
      <button className={`btn rec ${p.recording ? "on" : ""}`} onClick={p.onRecord} title="Record the master output to a 24-bit WAV">
        ● REC {p.recording ? `${mm}:${ss} · ${(p.recBytes / 1e6).toFixed(0)} MB` : ""}
      </button>
      <div className="export">
        <span className="lbl">SET</span>
        <a className="btn small" href={api.exportUrl("txt")}>TXT</a>
        <a className="btn small" href={api.exportUrl("json")}>JSON</a>
      </div>
      <div className="spacer" />
      <div className="status-dots">
        <span className={`dot ${p.wsUp ? "ok" : "bad"}`} title="backend link" /> <span className="lbl">LINK</span>
        <span className={`dot ${p.jevOnline ? "ok" : "warn"}`} title={p.jevOnline ? "Jev online" : "no Jev key - rules only"} /> <span className="lbl">JEV</span>
        <span className="lbl mono dim">{(p.sampleRate / 1000).toFixed(1)}k · 32f</span>
      </div>
      <div className="master-bpm">
        <span className="lbl">BPM</span>
        <span className="mono">{p.masterBpm ? p.masterBpm.toFixed(1) : "---.-"}</span>
      </div>
    </header>
  );
}
