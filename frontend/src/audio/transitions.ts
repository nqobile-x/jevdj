/**
 * Transition runner: turns a plan into beat-aligned parameter automation on both decks.
 * T0 is the context time at which the outgoing track hits its mix-out downbeat. Every event is
 * placed on T0 + k bars, so the incoming downbeat lands on an outgoing downbeat.
 */

import type { TransitionPlan, TransitionStyle } from "../types";
import { KILL_DB, type Deck } from "./deck";
import type { Engine } from "./engine";

export const STYLE_BARS: Record<TransitionStyle, number> = {
  long_blend: 32, quick_cut: 4, filter_fade: 16, echo_out: 8, wash_out: 8, brake: 2, loop_roll: 4,
  chop: 8, tease: 8, rewind: 2,
};

export interface ScheduledTransition {
  style: TransitionStyle;
  t0: number; // context time of the mix-out downbeat
  bar: number; // seconds per bar at the playing tempo
  bars: number;
  inStart: number; // context time the incoming deck starts
  end: number; // context time the outgoing deck can be stopped
}

/** Bar (relative to T0) at which the incoming track starts, per style. */
export function incomingOffsetBars(style: TransitionStyle, bars: number): number {
  switch (style) {
    case "quick_cut":
    case "loop_roll":
    case "tease":
      return bars;
    case "brake":
    case "rewind":
      return 1;
    case "echo_out":
    case "wash_out":
      return 2;
    default:
      return 0;
  }
}

export function bpmRatio(outBpm: number, inBpm: number): number {
  let best = Infinity;
  for (const m of [0.5, 1, 2]) {
    const r = outBpm / (inBpm * m);
    if (Math.abs(r - 1) < Math.abs(best - 1)) best = r;
  }
  return best;
}

/** Playback rate that syncs the incoming deck to the outgoing deck's current tempo. */
export function syncRate(out: Deck, inc: Deck): number {
  if (!out.track || !inc.track) return 1;
  const r = bpmRatio(out.bpm, inc.track.bpm);
  return Math.abs(r - 1) <= 0.085 ? r : 1; // small headroom for bridged tempos
}

export function scheduleTransition(
  engine: Engine,
  out: Deck,
  inc: Deck,
  style: TransitionStyle,
  bars: number,
  mixOutPos: number,
  mixInPos: number,
): ScheduledTransition {
  const t0 = out.timeAt(mixOutPos);
  const bar = out.barLength();
  const inStart = t0 + incomingOffsetBars(style, bars) * bar;
  const end = t0 + bars * bar;

  inc.setRate(syncRate(out, inc));
  inc.resetAutomation();
  out.resetAutomation(t0); // from the mix point on: keeps pre-routines (run it back) intact

  const o = { low: out.autoLow.gain, high: out.autoHigh.gain, hp: out.autoHP.frequency, mix: out.mix.gain,
    dry: out.dry.gain, send: out.echoSend.gain, fb: out.echoFeedback.gain, rev: out.reverbSend.gain };
  const i = { low: inc.autoLow.gain, high: inc.autoHigh.gain, mix: inc.mix.gain };
  const at = (b: number) => t0 + b * bar;

  switch (style) {
    case "long_blend": {
      // Incoming fades in with no bass; bass swap at the halfway phrase; outgoing fades out after.
      const half = bars / 2;
      i.mix.setValueAtTime(0, at(0));
      i.mix.linearRampToValueAtTime(1, at(half));
      i.low.setValueAtTime(KILL_DB, at(0));
      i.high.setValueAtTime(-6, at(0));
      i.high.linearRampToValueAtTime(0, at(half));
      // Bass swap over one beat, exactly on the halfway downbeat.
      o.low.setValueAtTime(0, at(half) - bar / 4);
      o.low.linearRampToValueAtTime(KILL_DB, at(half));
      i.low.setValueAtTime(KILL_DB, at(half) - bar / 4);
      i.low.linearRampToValueAtTime(0, at(half));
      o.high.setValueAtTime(0, at(half));
      o.high.linearRampToValueAtTime(-12, at(bars));
      o.mix.setValueAtTime(1, at(half));
      o.mix.linearRampToValueAtTime(0, at(bars));
      break;
    }
    case "quick_cut": {
      // Outgoing builds with a rising high-pass and a noise riser, then a hard cut on the downbeat.
      o.hp.setValueAtTime(10, at(0));
      o.hp.exponentialRampToValueAtTime(900, at(bars) - 0.01);
      o.mix.setValueAtTime(1, at(bars) - 0.012);
      o.mix.linearRampToValueAtTime(0, at(bars));
      i.mix.setValueAtTime(1, inStart);
      engine.riser(at(Math.max(0, bars - 2)), 2 * bar, 0.16);
      break;
    }
    case "loop_roll": {
      // Build: high-pass climbs and a riser swells, the last bar stutters (1/2, 1/4, 1/8), then drop.
      o.hp.setValueAtTime(10, at(0));
      o.hp.exponentialRampToValueAtTime(600, at(bars) - 0.01);
      out.scheduleRoll(at(bars - 1));
      o.mix.setValueAtTime(1, at(bars) - 0.012);
      o.mix.linearRampToValueAtTime(0, at(bars));
      i.mix.setValueAtTime(1, inStart);
      engine.riser(at(0), bars * bar, 0.24);
      break;
    }
    case "brake": {
      // Turntable stop over the last 2 beats of bar 1, a breath of silence, then slam the new track.
      out.scheduleBrake(at(1) - bar / 2, bar / 2 - 0.02);
      o.mix.setValueAtTime(1, at(1) - 0.02);
      o.mix.linearRampToValueAtTime(0, at(1));
      i.mix.setValueAtTime(1, inStart);
      break;
    }
    case "chop": {
      // Trade bars like a human on the faders: B 2, A 2, B 1, A 1, B 1, A 1, then land on B's drop.
      const segs: ["A" | "B", number][] = [["B", 2], ["A", 2], ["B", 1], ["A", 1], ["B", 1], ["A", 1]];
      let b0 = 0;
      for (const [who, len] of segs) {
        const t = at(b0);
        const inOn = who === "B" ? 1 : 0;
        i.mix.setValueAtTime(1 - inOn, t - 0.006);
        i.mix.linearRampToValueAtTime(inOn, t);
        o.mix.setValueAtTime(inOn, t - 0.006);
        o.mix.linearRampToValueAtTime(1 - inOn, t);
        b0 += len;
      }
      i.mix.setValueAtTime(0, at(bars) - 0.006);
      i.mix.linearRampToValueAtTime(1, at(bars));
      o.mix.setValueAtTime(1, at(bars) - 0.006);
      o.mix.linearRampToValueAtTime(0, at(bars));
      break;
    }
    case "tease": {
      // Stabs of the new drop over the outgoing, building (1 beat, 1 beat, 2, 2, a full bar), then drop.
      const beat = bar / 4; // context seconds
      const beatTrack = inc.beatPeriod; // track seconds
      const stabs: [number, number][] = [[1, 1], [3, 1], [5, 2], [6, 2], [7, 4]]; // [bar, beats]
      for (const [b, beats] of stabs) {
        inc.scheduleSlice(at(b), mixInPos, beats * beatTrack);
        o.mix.setValueAtTime(1, at(b));
        o.mix.linearRampToValueAtTime(0.55, at(b) + 0.01);
        o.mix.setValueAtTime(0.55, at(b) + beats * beat - 0.01);
        o.mix.linearRampToValueAtTime(1, at(b) + beats * beat);
      }
      o.low.setValueAtTime(0, at(7));
      o.low.linearRampToValueAtTime(KILL_DB, at(7) + beat);
      o.mix.setValueAtTime(1, at(bars) - 0.012);
      o.mix.linearRampToValueAtTime(0, at(bars));
      i.mix.setValueAtTime(1, inStart);
      engine.riser(at(bars - 2), 2 * bar, 0.14);
      break;
    }
    case "rewind": {
      // Spin the outgoing back over the last 2 beats of bar 1, then slam the new track in on its drop.
      out.scheduleRewind(at(1) - bar / 2, bar / 2 - 0.01);
      o.mix.setValueAtTime(1, at(1) - 0.01);
      o.mix.linearRampToValueAtTime(0, at(1));
      i.mix.setValueAtTime(1, inStart);
      break;
    }
    case "wash_out": {
      // Reverb swells on the outgoing while the high-pass thins it out; the new track rises out of the wash.
      o.rev.setValueAtTime(0, at(0));
      o.rev.linearRampToValueAtTime(1, at(2));
      o.hp.setValueAtTime(10, at(0));
      o.hp.exponentialRampToValueAtTime(1200, at(2));
      o.dry.setValueAtTime(1, at(2) - 0.05);
      o.dry.linearRampToValueAtTime(0, at(2));
      o.rev.setValueAtTime(1, at(2));
      o.rev.linearRampToValueAtTime(0, at(2) + 0.1);
      i.mix.setValueAtTime(0, inStart);
      i.mix.linearRampToValueAtTime(1, inStart + 2 * bar);
      i.low.setValueAtTime(KILL_DB, inStart);
      i.low.linearRampToValueAtTime(0, inStart + 2 * bar);
      o.mix.setValueAtTime(1, at(bars) - bar);
      o.mix.linearRampToValueAtTime(0, at(bars));
      break;
    }
    case "filter_fade": {
      // High-pass sweeps the outgoing away over the whole phrase; incoming comes up underneath.
      const half = bars / 2;
      o.hp.setValueAtTime(10, at(0));
      o.hp.exponentialRampToValueAtTime(2500, at(bars));
      o.mix.setValueAtTime(1, at(bars - 4));
      o.mix.linearRampToValueAtTime(0, at(bars));
      i.mix.setValueAtTime(0, at(0));
      i.mix.linearRampToValueAtTime(1, at(half));
      i.low.setValueAtTime(KILL_DB, at(0));
      i.low.setValueAtTime(KILL_DB, at(half) - bar / 4);
      i.low.linearRampToValueAtTime(0, at(half));
      break;
    }
    case "echo_out": {
      // One-beat echo on the last bar of the outgoing, dry signal cut, tail rings out, new track drops.
      out.echoDelay.delayTime.setValueAtTime(bar / 4, at(0));
      o.send.setValueAtTime(0, at(0));
      o.send.linearRampToValueAtTime(0.9, at(1));
      o.fb.setValueAtTime(0.55, at(0));
      o.dry.setValueAtTime(1, at(1) - 0.01);
      o.dry.linearRampToValueAtTime(0, at(1));
      o.send.setValueAtTime(0.9, at(1));
      o.send.linearRampToValueAtTime(0, at(1) + 0.02);
      o.fb.setValueAtTime(0.55, at(2));
      o.fb.linearRampToValueAtTime(0, at(bars));
      o.mix.setValueAtTime(1, at(bars) - bar);
      o.mix.linearRampToValueAtTime(0, at(bars));
      i.mix.setValueAtTime(1, inStart);
      break;
    }
  }

  inc.play(inStart, mixInPos);
  return { style, t0, bar, bars, inStart, end };
}

/**
 * Execute a backend plan: optional run-it-back pre-routine, outro loop, then the transition.
 * Used by the live auto mix AND the offline pre-listen, so what Jev checks is what you hear.
 */
export function runPlan(
  engine: Engine,
  out: Deck,
  inc: Deck,
  plan: TransitionPlan,
  mixOut: number,
  allowPre: boolean,
): ScheduledTransition {
  if (plan.pre?.type === "run_it_back" && allowPre && Math.abs(mixOut - plan.mix_out_s) < 0.01) {
    // Spin back over the last 2 beats, hot-cue to the hook, play it again, then do the transition.
    const t0 = out.timeAt(mixOut);
    const beat = out.barLength() / 4;
    out.scheduleRewind(t0 - 2 * beat, 2 * beat - 0.01, 4);
    out.scheduleJump(t0, plan.pre.jump_to_s);
    mixOut = plan.pre.jump_to_s + plan.pre.bars * 4 * out.beatPeriod;
  } else if (plan.loop && mixOut <= plan.loop.start_s + 0.01) {
    out.setLoop(plan.loop.start_s, plan.loop.end_s);
  }
  return scheduleTransition(engine, out, inc, plan.style, plan.bars, mixOut, plan.mix_in_s);
}
