/**
 * One deck:
 *
 *   source -> srcGain -> trim -> user EQ -> user filter -> auto EQ -> auto HP -+-> dry ---------+-> mix -> fader -> xfade -> music bus
 *                 |                                                         +-> echo send -> delay (feedback) -+
 *                 +-> kick tap (low-pass) -> beat lock                      +-> reverb send -> engine reverb
 *
 * User controls and transition automation use separate nodes so the auto-mixer never fights the knobs.
 */

import { api } from "../api";
import { Store } from "../store";
import type { DeckId, TrackDetail } from "../types";
import type { Engine } from "./engine";

export const KILL_DB = -40;

export interface DeckState {
  id: DeckId;
  track: TrackDetail | null;
  loading: boolean;
  error: string | null;
  playing: boolean;
  duration: number;
  rate: number; // playbackRate, 1 = native tempo
  cue: number;
  eq: { high: number; mid: number; low: number }; // -1 (kill) .. 0 .. +1 (+6 dB)
  filter: number; // -1 low-pass .. 0 off .. +1 high-pass
  gain: number; // trim, 0..1.5
  volume: number; // channel fader 0..1
  peaks: Float32Array[] | null;
  loadedAt: number;
  loop: { start: number; end: number } | null;
  fx: { echo: boolean; wash: boolean; roll: boolean };
}

export function eqToDb(v: number): number {
  return v >= 0 ? v * 6 : -Math.pow(-v, 1.5) * -KILL_DB;
}

interface Segment {
  ctx: number; // context time this segment starts
  pos: number; // track position at that time
  rate: number;
}

export class Deck {
  readonly store: Store<DeckState>;
  buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  private segments: Segment[] = [{ ctx: 0, pos: 0, rate: 1 }];
  private rollSlip: number | null = null; // where the track would be without the roll (slip mode)
  onEnded: (() => void) | null = null;

  readonly srcGain: GainNode;
  readonly tap: BiquadFilterNode; // low-passed signal for kick detection
  // user chain
  readonly trim: GainNode;
  readonly eqLow: BiquadFilterNode;
  readonly eqMid: BiquadFilterNode;
  readonly eqHigh: BiquadFilterNode;
  readonly hp: BiquadFilterNode;
  readonly lp: BiquadFilterNode;
  // transition automation chain
  readonly autoLow: BiquadFilterNode;
  readonly autoHigh: BiquadFilterNode;
  readonly autoHP: BiquadFilterNode;
  readonly dry: GainNode;
  readonly echoSend: GainNode;
  readonly echoDelay: DelayNode;
  readonly echoFeedback: GainNode;
  readonly reverbSend: GainNode;
  readonly mix: GainNode; // transition volume
  readonly fader: GainNode;
  readonly xfade: GainNode;

  constructor(readonly id: DeckId, private engine: Engine) {
    const ctx = engine.ctx;
    this.srcGain = new GainNode(ctx, { gain: 1 });
    this.tap = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 140, Q: 0.7 });
    this.trim = new GainNode(ctx, { gain: 1 });
    this.eqLow = new BiquadFilterNode(ctx, { type: "lowshelf", frequency: 220 });
    this.eqMid = new BiquadFilterNode(ctx, { type: "peaking", frequency: 1000, Q: 0.7 });
    this.eqHigh = new BiquadFilterNode(ctx, { type: "highshelf", frequency: 3500 });
    this.hp = new BiquadFilterNode(ctx, { type: "highpass", frequency: 10, Q: 0.9 });
    this.lp = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 22000, Q: 0.9 });
    this.autoLow = new BiquadFilterNode(ctx, { type: "lowshelf", frequency: 220 });
    this.autoHigh = new BiquadFilterNode(ctx, { type: "highshelf", frequency: 3500 });
    this.autoHP = new BiquadFilterNode(ctx, { type: "highpass", frequency: 10, Q: 1.2 });
    this.dry = new GainNode(ctx, { gain: 1 });
    this.echoSend = new GainNode(ctx, { gain: 0 });
    this.echoDelay = new DelayNode(ctx, { maxDelayTime: 4, delayTime: 0.5 });
    this.echoFeedback = new GainNode(ctx, { gain: 0 });
    this.reverbSend = new GainNode(ctx, { gain: 0 });
    this.mix = new GainNode(ctx, { gain: 1 });
    this.fader = new GainNode(ctx, { gain: 1 });
    this.xfade = new GainNode(ctx, { gain: Math.SQRT1_2 });

    this.srcGain.connect(this.trim);
    this.srcGain.connect(this.tap);
    this.trim.connect(this.eqLow).connect(this.eqMid).connect(this.eqHigh).connect(this.hp).connect(this.lp)
      .connect(this.autoLow).connect(this.autoHigh).connect(this.autoHP);
    this.autoHP.connect(this.dry).connect(this.mix);
    this.autoHP.connect(this.echoSend).connect(this.echoDelay);
    this.echoDelay.connect(this.echoFeedback).connect(this.echoDelay);
    this.echoDelay.connect(this.mix);
    this.autoHP.connect(this.reverbSend).connect(engine.reverb);
    this.mix.connect(this.fader).connect(this.xfade).connect(engine.musicBus);

    this.store = new Store<DeckState>({
      id, track: null, loading: false, error: null, playing: false, duration: 0, rate: 1, cue: 0,
      eq: { high: 0, mid: 0, low: 0 }, filter: 0, gain: 1, volume: 1, peaks: null, loadedAt: 0, loop: null,
      fx: { echo: false, wash: false, roll: false },
    });
  }

  get state(): DeckState {
    return this.store.get();
  }

  get track(): TrackDetail | null {
    return this.state.track;
  }

  get playing(): boolean {
    return this.state.playing;
  }

  get rate(): number {
    return this.state.rate;
  }

  /** Effective tempo right now (native BPM x pitch). */
  get bpm(): number {
    return (this.track?.bpm ?? 0) * this.rate;
  }

  get beatPeriod(): number {
    return this.track?.beat_period || 60 / (this.track?.bpm || 120);
  }

  private get seg(): Segment {
    return this.segments[this.segments.length - 1];
  }

  private anchor(ctx: number, pos: number, rate = this.state.rate, reset = false): void {
    if (reset) this.segments = [];
    this.segments.push({ ctx, pos, rate });
    if (this.segments.length > 32) this.segments.shift();
  }

  /** Track position without loop wrapping (where the needle would be in slip mode). */
  rawPosition(at = this.engine.now): number {
    let s = this.seg;
    for (let i = this.segments.length - 1; i > 0 && this.segments[i].ctx > at; i--) s = this.segments[i - 1];
    if (!this.state.playing || at < s.ctx) return s.pos;
    return Math.max(0, s.pos + (at - s.ctx) * s.rate);
  }

  /** Playhead in track seconds at context time `at` (handles tempo changes and loops). */
  position(at = this.engine.now): number {
    const raw = this.rawPosition(at);
    const loop = this.state.loop;
    if (loop && raw >= loop.end) return loop.start + ((raw - loop.start) % (loop.end - loop.start));
    return raw;
  }

  /** Context time at which the playhead reaches `pos` (assuming the rate holds). */
  timeAt(pos: number): number {
    return this.seg.ctx + (pos - this.seg.pos) / this.state.rate;
  }

  barLength(): number {
    return (4 * this.beatPeriod) / this.state.rate;
  }

  /** Beat phase 0..1 at a track position (0 = on the beat). */
  beatPhase(pos: number): number {
    const t = this.track;
    if (!t) return 0;
    const x = (pos - t.first_beat) / this.beatPeriod;
    return x - Math.floor(x);
  }

  /** Next bar boundary at or after `pos` on this track's grid. */
  nextDownbeat(pos: number): number {
    const t = this.track;
    if (!t) return pos;
    const bar = 4 * this.beatPeriod;
    const n = Math.ceil((pos - t.first_downbeat) / bar - 1e-6);
    return t.first_downbeat + Math.max(0, n) * bar;
  }

  async load(trackId: number): Promise<TrackDetail> {
    this.stop();
    this.store.set({ loading: true, error: null });
    try {
      const [detail, buf] = await Promise.all([
        api.track(trackId),
        fetch(api.audioUrl(trackId)).then(async (r) => {
          if (!r.ok) throw new Error(`audio ${r.status}`);
          return this.engine.ctx.decodeAudioData(await r.arrayBuffer());
        }),
      ]);
      this.buffer = buf;
      this.resetAutomation();
      this.anchor(this.engine.now, detail.first_downbeat || 0, 1, true);
      this.store.set({
        track: detail, loading: false, duration: buf.duration, rate: 1, cue: detail.first_downbeat || 0,
        peaks: computePeaks(buf, 4000), loadedAt: Date.now(), loop: null,
      });
      return detail;
    } catch (e) {
      this.store.set({ loading: false, error: (e as Error).message });
      throw e;
    }
  }

  /** Use an already-decoded buffer (offline pre-listen decks share the live decks' audio). */
  useBuffer(buf: AudioBuffer, detail: TrackDetail, rate = 1): void {
    this.buffer = buf;
    this.anchor(this.engine.now, detail.first_downbeat || 0, rate, true);
    this.store.set({ track: detail, duration: buf.duration, rate, cue: detail.first_downbeat || 0, loop: null });
  }

  /** Swap in a corrected beat grid (after a GRID fix) without reloading audio. */
  updateTrack(detail: TrackDetail): void {
    if (this.track?.id === detail.id) this.store.set({ track: detail });
  }

  /** Start at context time `at` from track position `offset`. */
  play(at = this.engine.now, offset = this.position()): void {
    if (!this.buffer) return;
    this.killSource();
    const start = Math.max(at, this.engine.now);
    const src = new AudioBufferSourceNode(this.engine.ctx, { buffer: this.buffer, playbackRate: this.state.rate });
    src.connect(this.srcGain);
    this.applyLoop(src);
    this.srcGain.gain.cancelScheduledValues(start);
    this.srcGain.gain.setValueAtTime(1, start);
    src.start(start, Math.max(0, offset));
    this.source = src;
    this.anchor(start, offset, this.state.rate, true);
    this.store.set({ playing: true });
    this.armEnded();
  }

  /** AUTO MATCH play: sync tempo, then start on the other deck's next downbeat, bar-aligned. */
  playSynced(other: Deck): void {
    if (!other.playing || !other.track || !this.track) return this.play();
    this.syncTempo(other);
    const otherPos = other.position(this.engine.now + 0.15);
    const at = other.timeAt(other.nextDownbeat(otherPos));
    this.play(at, this.nextDownbeat(Math.max(0, this.position() - 0.05)));
  }

  pause(): void {
    if (!this.state.playing) return;
    const pos = this.position();
    this.killSource();
    this.anchor(this.engine.now, pos, this.state.rate, true);
    this.store.set({ playing: false });
  }

  stop(): void {
    if (this.source) this.source.loop = false;
    this.store.set({ loop: null, fx: { echo: false, wash: false, roll: false } });
    this.pause();
    this.anchor(this.engine.now, this.track?.first_downbeat ?? 0, this.state.rate, true);
  }

  toggle(): void {
    if (this.state.playing) this.pause();
    else this.play();
  }

  seek(pos: number): void {
    pos = Math.min(Math.max(0, pos), this.state.duration);
    if (this.state.playing) this.play(this.engine.now, pos);
    else {
      this.anchor(this.engine.now, pos, this.state.rate, true);
      this.store.set({});
    }
  }

  /** Classic CDJ cue: while stopped, set cue here; while playing, jump back to cue and stop. */
  cue(): void {
    if (this.state.playing) {
      this.pause();
      this.anchor(this.engine.now, this.state.cue, this.state.rate, true);
      this.store.set({});
    } else {
      const snapped = this.nextDownbeat(Math.max(0, this.position() - 0.05));
      this.anchor(this.engine.now, snapped, this.state.rate, true);
      this.store.set({ cue: snapped });
    }
  }

  setRate(rate: number, at = this.engine.now): void {
    rate = Math.min(1.16, Math.max(0.84, rate));
    if (this.state.playing) {
      this.anchor(at, this.rawPosition(at), rate);
      this.source?.playbackRate.setValueAtTime(rate, at);
    }
    this.store.set({ rate });
  }

  /** Tempo sync to another deck (allowing half/double time). */
  syncTempo(other: Deck): void {
    if (!this.track || !other.track) return;
    let r = other.bpm / this.track.bpm;
    for (const m of [0.5, 2]) if (Math.abs(other.bpm / (this.track.bpm * m) - 1) < Math.abs(r - 1)) r = other.bpm / (this.track.bpm * m);
    if (Math.abs(r - 1) <= 0.16) this.setRate(r);
  }

  /** SYNC button: tempo, and if both are playing, beat phase too (jump by the phase difference). */
  sync(other: Deck): void {
    this.syncTempo(other);
    if (!this.playing || !other.playing || !this.track || !other.track) return;
    const now = this.engine.now + 0.05;
    let d = other.beatPhase(other.position(now)) - this.beatPhase(this.position(now));
    d -= Math.round(d);
    if (Math.abs(d) > 0.01) this.seek(this.position(now) + d * this.beatPeriod);
  }

  /** Move the beat grid (GRID buttons). Saved to the library so the fix sticks. */
  async shiftGrid(shiftS: number): Promise<void> {
    if (!this.track) return;
    this.updateTrack(await api.grid(this.track.id, shiftS, "user"));
  }

  setEq(band: "high" | "mid" | "low", v: number): void {
    const node = band === "high" ? this.eqHigh : band === "mid" ? this.eqMid : this.eqLow;
    node.gain.setTargetAtTime(eqToDb(v), this.engine.now, 0.01);
    this.store.set({ eq: { ...this.state.eq, [band]: v } });
  }

  setFilter(v: number): void {
    const t = this.engine.now;
    const lpHz = v < 0 ? 22000 * Math.pow(200 / 22000, -v) : 22000;
    const hpHz = v > 0 ? 10 * Math.pow(6000 / 10, v) : 10;
    this.lp.frequency.setTargetAtTime(lpHz, t, 0.02);
    this.hp.frequency.setTargetAtTime(hpHz, t, 0.02);
    this.store.set({ filter: v });
  }

  setGain(v: number): void {
    this.trim.gain.setTargetAtTime(v, this.engine.now, 0.01);
    this.store.set({ gain: v });
  }

  setVolume(v: number): void {
    this.fader.gain.setTargetAtTime(v * v, this.engine.now, 0.01);
    this.store.set({ volume: v });
  }

  // ---------------------------------------------------------------- loops

  /** Loop a beat-exact region (seamless because it is a whole number of beats on the grid). */
  setLoop(start: number, end: number): void {
    this.store.set({ loop: { start, end } });
    if (this.source) this.applyLoop(this.source);
  }

  /** LOOP button: loop `bars` bars from the bar the playhead is in; press again to release. */
  loopBars(bars: number): void {
    const t = this.track;
    if (!t) return;
    if (this.state.loop) return this.clearLoop();
    const bar = 4 * this.beatPeriod;
    const pos = this.position();
    const start = t.first_downbeat + Math.floor((pos - t.first_downbeat) / bar) * bar;
    this.setLoop(Math.max(0, start), Math.min(this.state.duration, start + bars * bar));
  }

  clearLoop(): void {
    if (!this.state.loop) return;
    if (this.state.playing) this.anchor(this.engine.now, this.position());
    if (this.source) this.source.loop = false;
    this.store.set({ loop: null });
  }

  private applyLoop(src: AudioBufferSourceNode): void {
    const loop = this.state.loop;
    src.loop = !!loop;
    if (loop) {
      src.loopStart = loop.start;
      src.loopEnd = loop.end;
    }
  }

  // ---------------------------------------------------------------- FX (manual, hold to engage)

  /** ECHO: 3/4-beat delay on the send, tail rings out after release. */
  fxEcho(on: boolean): void {
    const t = this.engine.now;
    if (on) this.echoDelay.delayTime.setValueAtTime((this.beatPeriod * 0.75) / this.rate, t);
    this.echoSend.gain.setTargetAtTime(on ? 0.7 : 0, t, 0.03);
    this.echoFeedback.gain.setTargetAtTime(on ? 0.5 : 0.35, t, 0.05);
    if (!on) this.echoFeedback.gain.setTargetAtTime(0, t + (this.beatPeriod * 8) / this.rate, 0.5);
    this.store.set({ fx: { ...this.state.fx, echo: on } });
  }

  /** WASH: send into the reverb. */
  fxWash(on: boolean): void {
    this.reverbSend.gain.setTargetAtTime(on ? 0.9 : 0, this.engine.now, on ? 0.15 : 0.4);
    this.store.set({ fx: { ...this.state.fx, wash: on } });
  }

  /** ROLL: stutter the next 1/4 beat while held; on release the track carries on where it would have been. */
  fxRoll(on: boolean, beats = 0.25): void {
    const t = this.track;
    if (!t || !this.playing) return;
    if (on) {
      const q = beats * this.beatPeriod;
      const pos = this.position(this.engine.now + 0.02);
      const start = t.first_beat + Math.ceil((pos - t.first_beat) / q) * q;
      this.rollSlip = 0;
      this.setLoop(start, start + q);
    } else if (this.rollSlip !== null) {
      this.rollSlip = null;
      const slip = this.rawPosition();
      this.store.set({ loop: null });
      this.seek(slip);
    }
    this.store.set({ fx: { ...this.state.fx, roll: on } });
  }

  /** BRAKE: turntable stop over `beats`, then pause and restore the pitch. */
  brake(beats = 2): void {
    if (!this.source || !this.playing) return;
    const t = this.engine.now;
    const dur = (beats * this.beatPeriod) / this.rate;
    this.scheduleBrake(t, dur);
    const rate = this.rate;
    setTimeout(() => {
      this.pause();
      this.setRate(rate);
    }, dur * 1000 + 30);
  }

  // ---------------------------------------------------------------- scheduled FX (transitions)

  scheduleBrake(at: number, dur: number): void {
    const pr = this.source?.playbackRate;
    if (!pr) return;
    pr.setValueAtTime(this.rate, at);
    pr.linearRampToValueAtTime(0.001, at + dur);
    this.srcGain.gain.setValueAtTime(1, at + dur * 0.8);
    this.srcGain.gain.linearRampToValueAtTime(0, at + dur);
  }

  /**
   * Loop roll over one bar starting at `at`: 2 beats of 1/2-beat repeats, then 1/4, then 1/8,
   * all sample-accurate (each repeat is its own scheduled slice of the buffer).
   */
  scheduleRoll(at: number): void {
    if (!this.buffer || !this.track) return;
    const ctx = this.engine.ctx;
    const beat = this.beatPeriod; // track seconds
    const beatCtx = beat / this.rate;
    const pos = this.position(at);
    const slice = this.track.first_beat + Math.floor((pos - this.track.first_beat) / beat) * beat;
    this.srcGain.gain.setValueAtTime(1, at - 0.002);
    this.srcGain.gain.linearRampToValueAtTime(0, at);
    const pattern: [number, number][] = [[0.5, 2], [0.25, 1], [0.125, 1]]; // [size in beats, length in beats]
    let t = at;
    for (const [size, len] of pattern) {
      for (let i = 0; i < len / size; i++) {
        const src = new AudioBufferSourceNode(ctx, { buffer: this.buffer, playbackRate: this.rate });
        src.connect(this.trim);
        src.start(t, slice, size * beat);
        src.onended = () => src.disconnect();
        t += size * beatCtx;
      }
    }
  }

  /**
   * Hot-cue jump at context time `at` to track position `pos`, sample-accurate: the new source
   * starts exactly as the old one stops. Used for "run it back" and skip-the-dull-part edits.
   */
  scheduleJump(at: number, pos: number): void {
    if (!this.buffer || !this.playing) return;
    const old = this.source;
    const src = new AudioBufferSourceNode(this.engine.ctx, { buffer: this.buffer, playbackRate: this.rate });
    src.connect(this.srcGain);
    src.start(at, Math.max(0, pos));
    this.srcGain.gain.setValueAtTime(1, at); // un-mute after a rewind lead-in
    if (old) {
      old.onended = null;
      try {
        old.stop(at);
      } catch {
        /* already stopped */
      }
      const o = old;
      setTimeout(() => o.disconnect(), Math.max(0, (at - this.engine.now) * 1000) + 200);
    }
    this.source = src;
    this.anchor(at, pos);
    this.armEnded();
  }

  /** One-off slice of this deck's track (tease stabs), through this deck's EQ and faders. */
  scheduleSlice(at: number, pos: number, durTrack: number): void {
    if (!this.buffer) return;
    const src = new AudioBufferSourceNode(this.engine.ctx, { buffer: this.buffer, playbackRate: this.rate });
    const env = new GainNode(this.engine.ctx, { gain: 0 });
    src.connect(env).connect(this.trim);
    const end = at + durTrack / this.rate;
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(1, at + 0.004);
    env.gain.setValueAtTime(1, end - 0.012);
    env.gain.linearRampToValueAtTime(0, end);
    src.start(at, Math.max(0, pos), durTrack + 0.05);
    src.onended = () => {
      src.disconnect();
      env.disconnect();
    };
  }

  /** Rewind: the last `beats` of audio played backwards, fast, over `dur` seconds; the track itself goes quiet. */
  scheduleRewind(at: number, dur: number, beats = 2): void {
    if (!this.buffer) return;
    const ctx = this.engine.ctx;
    const pos = this.position(at);
    const lenS = (beats * this.beatPeriod);
    const start = Math.max(0, pos - lenS);
    const rate = this.buffer.sampleRate;
    const n = Math.floor(lenS * rate);
    const rev = ctx.createBuffer(this.buffer.numberOfChannels, n, rate);
    const s0 = Math.floor(start * rate);
    for (let c = 0; c < this.buffer.numberOfChannels; c++) {
      const src = this.buffer.getChannelData(c).subarray(s0, s0 + n);
      const dst = rev.getChannelData(c);
      for (let i = 0; i < src.length; i++) dst[i] = src[src.length - 1 - i];
    }
    const node = new AudioBufferSourceNode(ctx, { buffer: rev, playbackRate: lenS / dur });
    node.playbackRate.setValueAtTime(lenS / dur * 0.6, at);
    node.playbackRate.linearRampToValueAtTime(lenS / dur * 1.6, at + dur);
    const env = new GainNode(ctx, { gain: 1 });
    env.gain.setValueAtTime(1, at + dur * 0.7);
    env.gain.linearRampToValueAtTime(0, at + dur);
    node.connect(env).connect(this.trim);
    this.srcGain.gain.setValueAtTime(1, at);
    this.srcGain.gain.linearRampToValueAtTime(0, at + 0.01);
    node.start(at);
    node.stop(at + dur + 0.02);
    node.onended = () => {
      node.disconnect();
      env.disconnect();
    };
  }

  resetAutomation(at = this.engine.now): void {
    for (const p of [this.autoLow.gain, this.autoHigh.gain]) {
      p.cancelScheduledValues(at);
      p.setValueAtTime(0, at);
    }
    for (const [p, v] of [[this.autoHP.frequency, 10], [this.mix.gain, 1], [this.dry.gain, 1],
      [this.echoSend.gain, 0], [this.echoFeedback.gain, 0], [this.reverbSend.gain, 0], [this.srcGain.gain, 1]] as const) {
      p.cancelScheduledValues(at);
      p.setValueAtTime(v, at);
    }
  }

  private killSource(): void {
    if (this.source) {
      this.source.onended = null;
      try {
        this.source.stop();
      } catch {
        /* not started */
      }
      this.source.disconnect();
      this.source = null;
    }
  }

  private armEnded(): void {
    const src = this.source;
    if (!src) return;
    src.onended = () => {
      if (this.source !== src) return;
      this.source = null;
      this.anchor(this.engine.now, this.state.duration, this.state.rate, true);
      this.store.set({ playing: false });
      this.onEnded?.();
    };
  }
}

/** Peak per bucket for wavesurfer (mono mixdown). */
export function computePeaks(buf: AudioBuffer, buckets: number): Float32Array[] {
  const chans = Array.from({ length: buf.numberOfChannels }, (_, i) => buf.getChannelData(i));
  const len = buf.length;
  const size = Math.max(1, Math.floor(len / buckets));
  const out = new Float32Array(buckets);
  for (let b = 0; b < buckets; b++) {
    let peak = 0;
    const start = b * size;
    const end = Math.min(len, start + size);
    for (let i = start; i < end; i += 4) {
      let s = 0;
      for (const c of chans) s += c[i];
      s = Math.abs(s / chans.length);
      if (s > peak) peak = s;
    }
    out[b] = peak;
  }
  return [out];
}
