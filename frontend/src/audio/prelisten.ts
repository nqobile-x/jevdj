/**
 * Pre-listen: Jev hears the mix before you do.
 *
 * The planned transition is rendered offline (faster than real time) with the SAME engine code as
 * the live mix, with the outgoing deck on the left channel and the incoming deck on the right.
 * Then we analyse it like a DJ's ears would:
 *   - beats: where each deck's kicks really land vs its grid -> the exact shift that lines them up
 *     (catches the classic half-beat trainwreck too)
 *   - loudness: incoming vs outgoing level -> gain match
 *   - key: harmonic consonance of the two layers while they overlap
 *   - vocals: two vocals over each other
 *   - gaps / jumps: dead air or sudden level drops, and clipping
 * The caller applies the corrections, re-renders to verify, and falls back to alternatives if needed.
 */

import type { TrackDetail, TransitionPlan } from "../types";
import { Deck } from "./deck";
import { Engine } from "./engine";
import { runPlan } from "./transitions";

const SR = 22050; // analysis rate: plenty for kicks and chroma, renders fast
const DROPS = new Set(["quick_cut", "loop_roll", "brake", "echo_out", "rewind", "tease", "wash_out"]);

export interface PrelistenReport {
  score: number; // 0..10
  issues: string[];
  fixes: string[];
  beatErrorMs: number | null; // incoming vs outgoing kick timing after the fix
  inShift: number; // track seconds to add to mix_in_s
  inGain: number; // multiply the incoming deck's gain by this
  keyConsonance: number | null; // -1..1
  renderMs: number;
}

export interface PrelistenInput {
  outBuf: AudioBuffer;
  outTrack: TrackDetail;
  outRate: number;
  inBuf: AudioBuffer;
  inTrack: TrackDetail;
  plan: TransitionPlan;
}

// ------------------------------------------------------------------ render

interface Rendered {
  out: Float32Array;
  inc: Float32Array;
  outDeck: Deck;
  incDeck: Deck;
  t0: number;
  inStart: number;
  end: number;
  bar: number;
}

async function render(input: PrelistenInput, plan: TransitionPlan): Promise<Rendered> {
  const { outBuf, outTrack, inBuf, inTrack } = input;
  const outRate = plan.tempo_ramp ? plan.tempo_ramp.out_rate : input.outRate;
  const barTrack = 4 * (outTrack.beat_period || 60 / outTrack.bpm);
  const startPos = Math.max(0, plan.mix_out_s - 2 * barTrack);
  const preBars = plan.pre ? plan.pre.bars : 0;
  const seconds = Math.min(110, ((plan.mix_out_s - startPos) + (plan.bars + preBars + 3) * barTrack) / outRate + 1);

  const ctx = new OfflineAudioContext(2, Math.ceil(seconds * SR), SR);
  const engine = new Engine(ctx);
  const merger = new ChannelMergerNode(ctx, { numberOfInputs: 2 });
  merger.connect(ctx.destination);
  engine.musicBus.disconnect();
  engine.musicBus.connect(merger, 0, 0); // FX returns (reverb, riser) belong to the outgoing side
  const outDeck = new Deck("A", engine);
  const incDeck = new Deck("B", engine);
  for (const [d, ch] of [[outDeck, 0], [incDeck, 1]] as const) {
    d.xfade.disconnect();
    d.xfade.connect(merger, 0, ch);
  }
  outDeck.useBuffer(outBuf, outTrack, outRate);
  incDeck.useBuffer(inBuf, inTrack, 1);
  outDeck.play(0, startPos);
  const sched = runPlan(engine, outDeck, incDeck, plan, plan.mix_out_s, true);
  const buf = await ctx.startRendering();
  return {
    out: buf.getChannelData(0), inc: buf.getChannelData(1), outDeck, incDeck,
    t0: sched.t0, inStart: sched.inStart, end: sched.end, bar: sched.bar,
  };
}

// ------------------------------------------------------------------ analysis helpers

function kicks(x: Float32Array, from = 0, to = Infinity): number[] {
  const a = 1 - Math.exp((-2 * Math.PI * 150) / SR);
  const hop = 64;
  let y = 0;
  let avg = 1e-6;
  let prev = 0;
  let last = -1;
  const out: number[] = [];
  const i0 = Math.max(0, Math.floor(from * SR));
  const i1 = Math.min(x.length, Math.floor(to * SR));
  for (let i = i0; i + hop <= i1; i += hop) {
    let e = 0;
    for (let j = i; j < i + hop; j++) {
      y += a * (x[j] - y);
      e += y * y;
    }
    e /= hop;
    const t = i / SR;
    if (e > avg * 5 && e > 1e-5 && e > prev && t - last > 0.15) {
      out.push(t);
      last = t;
    }
    avg = avg * 0.985 + e * 0.015;
    prev = e;
  }
  return out;
}

/** Median kick offset (context seconds) of a deck against its own beat grid, and how consistent it is. */
function gridOffset(times: number[], deck: Deck): { off: number; spread: number; n: number } | null {
  const t = deck.track;
  if (!t || times.length < 6) return null;
  const beat = deck.beatPeriod;
  const errs = times.map((ti) => {
    const x = (deck.position(ti) - t.first_beat) / beat;
    return (x - Math.round(x)) * beat / deck.rate;
  });
  const m = median(errs);
  const spread = median(errs.map((e) => Math.abs(e - m)));
  return { off: m, spread, n: errs.length };
}

function rmsDb(x: Float32Array, from: number, to: number): number {
  const i0 = Math.max(0, Math.floor(from * SR));
  const i1 = Math.min(x.length, Math.floor(to * SR));
  if (i1 <= i0) return -120;
  let s = 0;
  for (let i = i0; i < i1; i++) s += x[i] * x[i];
  return 10 * Math.log10(s / (i1 - i0) + 1e-12);
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// Tiny radix-2 FFT for chroma.
function fftMag(frame: Float32Array): Float32Array {
  const n = frame.length;
  const re = new Float32Array(n);
  const im = new Float32Array(n);
  for (let i = 0; i < n; i++) re[i] = frame[i] * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1)));
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    for (let i = 0; i < n; i += len) {
      for (let k = 0; k < len / 2; k++) {
        const wr = Math.cos(ang * k);
        const wi = Math.sin(ang * k);
        const ur = re[i + k];
        const ui = im[i + k];
        const vr = re[i + k + len / 2] * wr - im[i + k + len / 2] * wi;
        const vi = re[i + k + len / 2] * wi + im[i + k + len / 2] * wr;
        re[i + k] = ur + vr;
        im[i + k] = ui + vi;
        re[i + k + len / 2] = ur - vr;
        im[i + k + len / 2] = ui - vi;
      }
    }
  }
  const mag = new Float32Array(n / 2);
  for (let i = 0; i < n / 2; i++) mag[i] = Math.hypot(re[i], im[i]);
  return mag;
}

function chroma(x: Float32Array, at: number): number[] {
  const n = 4096;
  const i0 = Math.floor(at * SR);
  if (i0 + n > x.length) return new Array(12).fill(0);
  const mag = fftMag(x.subarray(i0, i0 + n));
  const c = new Array(12).fill(0);
  for (let k = 1; k < n / 2; k++) {
    const f = (k * SR) / n;
    if (f < 60 || f > 2000) continue;
    const pc = ((Math.round(12 * Math.log2(f / 440)) + 69) % 12 + 12) % 12;
    c[pc] += mag[k];
  }
  const norm = Math.hypot(...c) || 1;
  return c.map((v) => v / norm);
}

// How pleasant each interval sounds (semitones 0..11).
const INTERVAL = [1, -0.8, -0.3, 0.4, 0.5, 0.7, -1, 0.7, 0.5, 0.4, -0.3, -0.8];

function consonance(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < 12; i++) for (let j = 0; j < 12; j++) s += a[i] * b[j] * INTERVAL[(j - i + 12) % 12];
  return s;
}

// ------------------------------------------------------------------ analysis

function analyse(r: Rendered, plan: TransitionPlan, input: PrelistenInput): PrelistenReport {
  const issues: string[] = [];
  let score = 10;

  // Beats: each deck against its own grid (incoming only after it starts; outgoing before the mix).
  const outK = kicks(r.out, 0, r.t0 + (DROPS.has(plan.style) ? 0 : plan.bars * r.bar));
  const incK = kicks(r.inc, r.inStart + 0.05);
  const go = gridOffset(outK, r.outDeck);
  const gi = gridOffset(incK, r.incDeck);
  let inShift = 0;
  let beatErrorMs: number | null = null;
  if (go && gi && go.spread < 0.02 && gi.spread < 0.02) {
    const beatCtx = r.incDeck.beatPeriod / r.incDeck.rate;
    // Real gap between the decks' kicks = gap between their grids at the mix + each deck's kick-vs-grid offset.
    const tg = r.inStart + 0.5;
    let gridGap = r.outDeck.beatPhase(r.outDeck.position(tg)) - r.incDeck.beatPhase(r.incDeck.position(tg));
    gridGap -= Math.round(gridGap);
    let d = gridGap * beatCtx + (gi.off - go.off); // + = incoming kicks land late
    if (Math.abs(d) > beatCtx * 0.35) {
      issues.push("incoming beat grid is on the offbeat");
      d = d > 0 ? d - beatCtx / 2 : d + beatCtx / 2;
    }
    beatErrorMs = Math.round(d * 1000);
    if (Math.abs(d) > 0.004) inShift = d * r.incDeck.rate; // start further in = kicks earlier
    if (Math.abs(d) > 0.025) score -= 4;
    else if (Math.abs(d) > 0.012) score -= 2;
    else if (Math.abs(d) > 0.006) score -= 1;
  } else {
    score -= 0.5;
    issues.push("kicks not clear enough to measure (relying on the beat grid)");
  }

  // Loudness: incoming (after the mix) vs outgoing (before it).
  const outDb = rmsDb(r.out, Math.max(0, r.t0 - 2 * r.bar), r.t0);
  const inDb = rmsDb(r.inc, r.end, r.end + 2 * r.bar);
  let inGain = 1;
  if (outDb > -60 && inDb > -60 && Math.abs(inDb - outDb) > 1.5) {
    inGain = Math.min(1.5, Math.max(0.6, Math.pow(10, (outDb - inDb) / 20)));
    issues.push(`incoming ${inDb > outDb ? "louder" : "quieter"} by ${Math.abs(inDb - outDb).toFixed(1)} dB`);
  }

  // Key: only where both layers are clearly audible together.
  let keyCons: number | null = null;
  const both: number[] = [];
  for (let t = r.t0; t < r.end; t += 0.5) {
    if (rmsDb(r.out, t, t + 0.19) > -32 && rmsDb(r.inc, t, t + 0.19) > -32) both.push(t);
  }
  if (both.length >= 4) {
    keyCons = median(both.map((t) => consonance(chroma(r.out, t), chroma(r.inc, t))));
    if (keyCons < 0) {
      score -= 2.5;
      issues.push("keys clash while both tracks play");
    } else if (keyCons < 0.15) {
      score -= 1;
      issues.push("keys a little tense in the overlap");
    }
  }

  // Vocals over vocals during a real overlap.
  if (both.length >= 8) {
    const vOut = input.outTrack.vocal_bars ?? [];
    const vIn = input.inTrack.vocal_bars ?? [];
    const inBar0 = Math.max(0, Math.round((plan.mix_in_s - input.inTrack.first_downbeat) / (4 * r.incDeck.beatPeriod)));
    let clash = 0;
    for (let b = 0; b < Math.min(plan.bars, 16); b++) {
      if ((vOut[plan.out_bar + b] ?? 0) > 0.6 && (vIn[inBar0 + b] ?? 0) > 0.6) clash++;
    }
    if (clash >= 2) {
      score -= 1.5;
      issues.push(`vocals over vocals for ${clash} bars`);
    }
  }

  // Dead air and level cliffs in the combined mix.
  const beat = r.bar / 4;
  const allowSilence = plan.style === "brake" || plan.style === "rewind" ? 2 : 0;
  let silent = 0;
  let cliff = 0;
  let prevDb: number | null = null;
  for (let t = Math.max(0, r.t0 - r.bar); t < r.end + r.bar; t += beat) {
    const i0 = Math.floor(t * SR);
    const i1 = Math.min(r.out.length, Math.floor((t + beat) * SR));
    let s = 0;
    for (let i = i0; i < i1; i++) {
      const m = r.out[i] + r.inc[i];
      s += m * m;
    }
    const db = 10 * Math.log10(s / Math.max(1, i1 - i0) + 1e-12);
    if (db < -45) silent++;
    if (prevDb !== null && prevDb - db > 10 && db > -45) cliff++;
    prevDb = db;
  }
  if (silent > allowSilence) {
    score -= Math.min(3, silent - allowSilence);
    issues.push(`${silent - allowSilence} beat(s) of dead air`);
  }
  if (cliff > 1) {
    score -= 1;
    issues.push("sudden level drop");
  }

  // Clipping before the safety limiter.
  let over = 0;
  for (let i = 0; i < r.out.length; i++) if (Math.abs(r.out[i] + r.inc[i]) > 1) over++;
  if (over > SR * 0.01) {
    score -= 1;
    issues.push("mix runs hot (limiter would squash it)");
  }

  return {
    score: Math.max(0, Math.round(score * 10) / 10), issues, fixes: [], beatErrorMs, inShift, inGain,
    keyConsonance: keyCons === null ? null : Math.round(keyCons * 100) / 100, renderMs: 0,
  };
}

/**
 * Pre-listen a plan, apply beat and gain fixes, and verify by listening again.
 * Returns the (possibly corrected) plan and the final report.
 */
export async function prelisten(input: PrelistenInput): Promise<{ plan: TransitionPlan; report: PrelistenReport }> {
  const t0 = performance.now();
  let plan = { ...input.plan };
  let report = analyse(await render(input, plan), plan, input);
  const fixes: string[] = [];
  let inGain = 1;
  if (Math.abs(report.inShift) > 0.001) {
    const ms = Math.round((report.inShift / 1) * 1000);
    plan = { ...plan, mix_in_s: plan.mix_in_s + report.inShift };
    fixes.push(`beats lined up (${ms > 0 ? "+" : ""}${ms} ms)`);
    const again = analyse(await render(input, plan), plan, input);
    if (again.score >= report.score - 0.5) report = again; // keep the fix if it didn't make things worse
    else {
      plan = { ...input.plan };
      fixes.pop();
    }
  }
  if (report.inGain !== 1) {
    inGain = report.inGain;
    fixes.push(`gain matched (${(20 * Math.log10(inGain)).toFixed(1)} dB)`);
    report.score = Math.min(10, report.score + 0.5);
  }
  report = { ...report, fixes, inGain, renderMs: Math.round(performance.now() - t0) };
  return { plan, report };
}
