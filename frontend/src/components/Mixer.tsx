import { useEffect, useRef, useState } from "react";
import type { LockState } from "../audio/beatlock";
import { KILL_DB, type Deck } from "../audio/deck";
import { useStore, type Store } from "../store";
import type { TransitionStyle } from "../types";
import { Knob } from "./Knob";

/** What the auto-mixer is doing to a deck right now, in knob units (null = not automating). */
interface AutoView {
  low: number | null;
  high: number | null;
  filter: number | null;
  level: number; // transition volume 0..1
  echo: boolean;
  wash: boolean;
}

const dbToKnob = (db: number) => (db >= 0 ? db / 6 : -Math.pow(Math.min(1, -db / -KILL_DB), 1 / 1.5));

function readAuto(d: Deck): AutoView {
  const low = d.autoLow.gain.value;
  const high = d.autoHigh.gain.value;
  const hp = d.autoHP.frequency.value;
  return {
    low: Math.abs(low) > 0.5 ? dbToKnob(low) : null,
    high: Math.abs(high) > 0.5 ? dbToKnob(high) : null,
    filter: hp > 15 ? Math.log(hp / 10) / Math.log(600) : null,
    level: d.mix.gain.value * d.dry.gain.value,
    echo: d.echoSend.gain.value > 0.05,
    wash: d.reverbSend.gain.value > 0.05,
  };
}

/** Poll AudioParam values at ~30 fps so knobs visibly move during automated mixes. */
function useAutoView(deck: Deck): AutoView {
  const [v, setV] = useState<AutoView>(() => readAuto(deck));
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const loop = (t: number) => {
      if (t - last > 33) {
        last = t;
        const n = readAuto(deck);
        setV((o) => (JSON.stringify(o) === JSON.stringify(n) ? o : n));
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [deck]);
  return v;
}

function Channel({ deck, color, auto, automating }: { deck: Deck; color: string; auto: AutoView; automating: boolean }) {
  const st = useStore(deck.store);
  const db = (v: number) => (v >= 0 ? `+${(v * 6).toFixed(1)}` : v <= -0.99 ? "KILL" : `${(-Math.pow(-v, 1.5) * 40).toFixed(0)}`);
  const hold = (fx: (on: boolean) => void) => ({
    onPointerDown: () => fx(true),
    onPointerUp: () => fx(false),
    onPointerLeave: (e: React.PointerEvent) => e.buttons && fx(false),
  });
  return (
    <div className={`channel channel-${deck.id}`} style={{ ["--deck" as string]: color }}>
      <div className="knobs">
        <Knob label="GAIN" value={st.gain} min={0} max={1.5} center={1} color={color} onChange={(v) => deck.setGain(v)} />
        <Knob label="HI" value={st.eq.high} color={color} onChange={(v) => deck.setEq("high", v)} format={db} autoValue={auto.high} />
        <Knob label="MID" value={st.eq.mid} color={color} onChange={(v) => deck.setEq("mid", v)} format={db} />
        <Knob label="LOW" value={st.eq.low} color={color} onChange={(v) => deck.setEq("low", v)} format={db} autoValue={auto.low} />
        <Knob
          label="FILTER" value={st.filter} color={color} onChange={(v) => deck.setFilter(v)} autoValue={auto.filter}
          format={(v) => (Math.abs(v) < 0.02 ? "OFF" : v < 0 ? "LP" : "HP")}
        />
      </div>
      <div className="fx-pads">
        <button className={`pad ${st.fx.echo || auto.echo ? "on" : ""}`} {...hold((on) => deck.fxEcho(on))} title="Hold: echo">ECHO</button>
        <button className={`pad ${st.fx.wash || auto.wash ? "on" : ""}`} {...hold((on) => deck.fxWash(on))} title="Hold: reverb wash">WASH</button>
        <button className={`pad ${st.fx.roll ? "on" : ""}`} {...hold((on) => deck.fxRoll(on))} title="Hold: 1/4-beat roll">ROLL</button>
        <button className="pad" onClick={() => deck.brake()} title="Vinyl brake">BRAKE</button>
      </div>
      <div className="fader">
        <div className="fader-wrap">
          <input
            type="range" min={0} max={1} step={0.001} value={st.volume}
            onChange={(e) => deck.setVolume(Number(e.target.value))}
            onDoubleClick={() => deck.setVolume(1)}
          />
          {automating && <div className="auto-level" title="auto-mix level"><i style={{ height: `${Math.min(1, auto.level) * 100}%` }} /></div>}
        </div>
        <span className="lbl">VOL</span>
      </div>
    </div>
  );
}

function Meter({ level }: { level: () => number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let raf = 0;
    let shown = 0;
    const loop = () => {
      const v = level();
      shown = Math.max(v, shown * 0.92);
      if (ref.current) {
        const dbfs = 20 * Math.log10(shown + 1e-6);
        ref.current.style.setProperty("--lvl", `${Math.max(0, Math.min(1, (dbfs + 48) / 48)) * 100}%`);
        ref.current.classList.toggle("clip", shown > 0.98);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [level]);
  return <div className="meter" ref={ref}><div className="meter-fill" /></div>;
}

const MANUAL_STYLES: [TransitionStyle | "auto", string][] = [
  ["auto", "AUTO"], ["long_blend", "BLEND"], ["filter_fade", "FILTER"], ["quick_cut", "CUT"], ["echo_out", "ECHO"],
  ["wash_out", "WASH"], ["loop_roll", "ROLL"], ["brake", "BRAKE"],
  ["chop", "CHOP"], ["tease", "TEASE"], ["rewind", "REWIND"],
];

interface Props {
  a: Deck;
  b: Deck;
  crossfader: number;
  onCrossfader: (v: number) => void;
  level: () => number;
  mixProgress: number | null;
  lock: Store<LockState>;
  canManualMix: boolean;
  onManualMix: (style: TransitionStyle | "auto") => void;
}

export function Mixer({ a, b, crossfader, onCrossfader, level, mixProgress, lock, canManualMix, onManualMix }: Props) {
  const autoA = useAutoView(a);
  const autoB = useAutoView(b);
  const lk = useStore(lock);
  const [style, setStyle] = useState<TransitionStyle | "auto">("auto");
  // While automating, the ghost crossfader shows where a DJ's hand would be.
  const automating = mixProgress != null;
  const ghost = automating ? autoB.level / Math.max(0.001, autoA.level + autoB.level) : null;
  const lockCls = lk.errorMs == null ? "" : Math.abs(lk.errorMs) <= 5 ? "ok" : Math.abs(lk.errorMs) <= 15 ? "warn" : "bad";

  return (
    <div className="mixer">
      <Channel deck={a} color="var(--amber)" auto={autoA} automating={automating} />
      <div className="mixer-center">
        <Meter level={level} />
        <div className="xfader">
          <span className="lbl a">A</span>
          <div className="xfader-track">
            <input
              type="range" min={0} max={1} step={0.001} value={crossfader}
              onChange={(e) => onCrossfader(Number(e.target.value))}
              onDoubleClick={() => onCrossfader(0.5)}
            />
            {ghost !== null && <i className="xfader-ghost" style={{ left: `${ghost * 100}%` }} />}
          </div>
          <span className="lbl b">B</span>
        </div>
        <div className="mix-progress">
          {mixProgress != null ? (
            <><div className="bar" style={{ width: `${mixProgress * 100}%` }} /><span className="mono">MIXING {Math.round(mixProgress * 100)}%</span></>
          ) : (
            <span className="lbl">CROSSFADER</span>
          )}
        </div>
        <div className={`lock ${lk.active ? "active" : ""} ${lockCls}`} title="Live beat lock: kick-drum phase error between the decks">
          <span className="lbl">BEAT LOCK</span>
          <span className="mono">
            {!lk.active ? "--" : lk.errorMs == null ? "LISTENING" : `${lk.errorMs > 0 ? "+" : ""}${lk.errorMs} ms`}
          </span>
        </div>
        <div className="manual-mix">
          <select value={style} onChange={(e) => setStyle(e.target.value as TransitionStyle | "auto")}
            title="Transition style - AUTO lets Jev pick the move for these two tracks">
            {MANUAL_STYLES.map(([s, l]) => <option key={s} value={s}>{l}</option>)}
          </select>
          <button className="btn small go" disabled={!canManualMix} onClick={() => onManualMix(style)}
            title="Mix from the playing deck into the other deck now (AUTO off)">MIX ▶</button>
        </div>
      </div>
      <Channel deck={b} color="var(--cyan)" auto={autoB} automating={automating} />
    </div>
  );
}
