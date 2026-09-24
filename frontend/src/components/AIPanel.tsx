import type { AutoState } from "../audio/autodj";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Decision, LearnedStyle, RadioStatus } from "../types";

const STYLE_LABEL: Record<string, string> = {
  long_blend: "LONG BLEND", quick_cut: "QUICK CUT", filter_fade: "FILTER FADE", echo_out: "ECHO OUT",
  wash_out: "WASH OUT", brake: "BRAKE", loop_roll: "LOOP ROLL", chop: "CHOP", tease: "TEASE", rewind: "REWIND",
};

const KIND_LABEL: Record<string, string> = {
  next_track: "NEXT", smoothness: "SMOOTH", transition_style: "STYLE", vocal_check: "VOCALS",
  set_phase: "PHASE", override: "OVERRIDE", voice: "VOICE", source: "SOURCE", grid_fix: "GRID", feedback: "RATED",
  prelisten: "EARS",
};

function Conf({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="conf none">--</span>;
  const cls = value >= 0.75 ? "hi" : value >= 0.55 ? "mid" : "lo";
  return (
    <span className={`conf ${cls}`}>
      <i style={{ width: `${value * 100}%` }} />
      <b className="mono">{value.toFixed(2)}</b>
    </span>
  );
}

function answerText(a: unknown): string {
  if (a == null) return "-";
  return typeof a === "string" ? a : JSON.stringify(a);
}

interface Props {
  auto: AutoState;
  decisions: Decision[];
  onVeto: () => void;
  onPickAnother: () => void;
  onMixNow: () => void;
  onSwitch: () => void;
  radio: RadioStatus | null;
  onRate: (v: 1 | -1) => void;
}

function Learned({ onClose }: { onClose: () => void }) {
  const [styles, setStyles] = useState<Record<string, LearnedStyle> | null>(null);
  useEffect(() => {
    void api.learning().then((l) => setStyles(l.styles)).catch(() => setStyles({}));
  }, []);
  const rows = Object.entries(styles ?? {}).sort((a, b) => b[1].bonus - a[1].bonus);
  return (
    <div className="learned">
      <div className="learned-head">
        <span className="lbl">WHAT JEV LEARNED</span>
        <button className="btn tiny warn" onClick={() => void api.resetLearning().then(() => setStyles({}))}>RESET</button>
        <button className="btn tiny" onClick={onClose}>CLOSE</button>
      </div>
      {rows.length === 0 ? <div className="dim">Nothing yet. Rate mixes with 🔥 / 👎 and Jev leans toward what you like.</div> : rows.map(([s, v]) => (
        <div key={s} className="learned-row">
          <span>{STYLE_LABEL[s] ?? s}</span>
          <span className="mono dim">🔥 {v.up} · 👎 {v.down}</span>
          <span className="bias"><i style={{ left: `${50 + v.bonus * 100}%` }} /></span>
        </div>
      ))}
      <div className="dim small">Preferences only nudge Jev's choice. Musical safety rules (keys, tempo) always win.</div>
    </div>
  );
}

export function AIPanel({ auto, decisions, onVeto, onPickAnother, onMixNow, onSwitch, radio, onRate }: Props) {
  const [showLearned, setShowLearned] = useState(false);
  const next = auto.next;
  const plan = auto.plan;
  const canOverride = auto.enabled && auto.status === "ready";
  return (
    <section className="ai panel">
      <header className="panel-head">
        <span className="panel-title">AI PANEL</span>
        <span className={`status status-${auto.status}`}>{auto.status.toUpperCase()}</span>
        {auto.message && <span className="dim ellipsis">{auto.message}</span>}
      </header>

      <div className="ai-now">
        <div className="row">
          <span className="lbl">FROM</span>
          <span className="val ellipsis">
            {radio?.active
              ? <>AUDIUS · {radio.query ?? `trending ${radio.genre ?? ""}`} <span className="dim">({radio.ready} ready{radio.adding ? `, preparing ${radio.adding}` : ""})</span></>
              : "YOUR LIBRARY"}
          </span>
        </div>
        <div className="row">
          <span className="lbl">NEXT</span>
          <span className="val ellipsis">{next ? `${next.track.artist ? next.track.artist + " - " : ""}${next.track.title}` : "-"}</span>
          {next && <span className={`src src-${auto.nextManual ? "user" : next.source}`}>{auto.nextManual ? "YOU" : next.source.toUpperCase()}</span>}
          <Conf value={next?.confidence} />
        </div>
        <div className="row">
          <span className="lbl">WHY</span>
          <span className="val ellipsis dim">{next ? next.reason + (next.fallback ? ` (${next.fallback})` : "") : "-"}</span>
        </div>
        <div className="row">
          <span className="lbl">MIX</span>
          <span className="val">
            {plan ? `${STYLE_LABEL[plan.style]} · ${plan.bars} bars` : "-"}
            {plan?.shifted_for_vocals && <span className="tag">VOCAL SHIFT</span>}
          </span>
          {plan && <span className={`src src-${plan.source}`}>{plan.source.toUpperCase()}</span>}
          <Conf value={plan?.confidence} />
        </div>
        <div className="row" title="Jev renders the next mix in advance and listens to it: beats, loudness, keys, vocals, gaps">
          <span className="lbl">EARS</span>
          <span className="val ellipsis">
            {!auto.ears ? <span className="dim">-</span>
              : auto.ears.status === "listening" ? <span className="hot">pre-listening the next mix...</span>
              : auto.ears.status === "skipped" ? <span className="dim">no time to pre-listen this one</span>
              : <>
                {auto.ears.switchedFrom && <span className="tag">CHANGED PLAN</span>}{" "}
                {[...(auto.ears.report?.fixes ?? []), ...(auto.ears.report?.issues ?? []).map((i) => `heard: ${i}`)].join(" · ") || "sounds clean"}
              </>}
          </span>
          {auto.ears?.report && <Conf value={auto.ears.report.score / 10} />}
        </div>
        <div className="countdown">
          {auto.status === "mixing" ? (
            <span className="mono hot">MIXING NOW</span>
          ) : auto.barsToMix != null ? (
            <><span className="mono big">{Math.ceil(auto.barsToMix)}</span><span className="lbl">BARS TO MIX</span></>
          ) : (
            <span className="lbl">{auto.enabled ? "waiting for a plan" : "AUTO is off"}</span>
          )}
          {auto.lastVoice && <span className="voice-line ellipsis">“{auto.lastVoice.text}”</span>}
        </div>
        <div className="rate-row">
          <span className="lbl">LAST MIX</span>
          <span className="val ellipsis">{auto.lastMix ? `${STYLE_LABEL[auto.lastMix.style] ?? auto.lastMix.style} into ${auto.lastMix.into}` : "-"}</span>
          <button className={`btn tiny ${auto.lastMix?.rated === 1 ? "on-fire" : ""}`} disabled={!auto.lastMix} onClick={() => onRate(1)} title="That was fire: do more like it">🔥</button>
          <button className={`btn tiny ${auto.lastMix?.rated === -1 ? "on-nah" : ""}`} disabled={!auto.lastMix} onClick={() => onRate(-1)} title="Nah: do less of that">👎</button>
          <button className="btn tiny" onClick={() => setShowLearned((v) => !v)} title="What Jev has learned from your ratings">LEARNED</button>
        </div>
        {showLearned && <Learned onClose={() => setShowLearned(false)} />}
        <div className="ai-buttons">
          <button className="btn warn" disabled={!canOverride} onClick={onVeto}>VETO</button>
          <button className="btn" disabled={!canOverride} onClick={onPickAnother}>PICK ANOTHER</button>
          <button className="btn go" disabled={!canOverride} onClick={onMixNow}>MIX NOW</button>
          <button className="btn switch" disabled={!canOverride} onClick={onSwitch} title="Take the set in a new direction">
            SWITCH IT UP
          </button>
        </div>
      </div>

      <div className="log">
        {decisions.length === 0 && <div className="empty dim">Every Jev decision shows up here with its confidence.</div>}
        {decisions.map((d) => (
          <div key={d.id} className={`log-row src-${d.source}`} title={d.question ?? ""}>
            <span className="mono dim">{new Date(d.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
            <span className="kind">{KIND_LABEL[d.kind] ?? d.kind.toUpperCase()}</span>
            <span className={`src src-${d.source}`}>{d.source.toUpperCase()}</span>
            <span className="ans ellipsis">{answerText(d.answer)}{d.reason ? <span className="dim"> - {d.reason}</span> : null}</span>
            <Conf value={d.confidence} />
          </div>
        ))}
      </div>
    </section>
  );
}
