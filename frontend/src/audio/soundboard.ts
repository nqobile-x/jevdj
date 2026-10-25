/**
 * Smart DJ Soundboard & Stem/Chunk Mashup Sampler.
 *
 * 1. Trending SA & International Club FX Pads (synthesised sample-accurately in 32-bit float):
 *    - AIR HORN ("Prrr-papa-pa!" pitch-bent saw/square stack + dub delay)
 *    - DUB SIREN (Reggae/Amapiano LFO square/triangle siren through dub echo)
 *    - LOG DRUM (Signature SA Amapiano pitch-envelope sub-percussion slam)
 *    - WHISTLE (SA Kasi/Festival trill whistle)
 *    - LASER ZAP (Downward exponential FM synth laser)
 *    - SUB DROP (808/festival sub-bass boom 110 Hz -> 28 Hz)
 *    - YEBO! / EISH! (Vocal hype chop / crowd chant formant burst)
 *    - REWIND FX (Quick vinyl spin-back scratch sound)
 *
 * 2. Smart Chunk / Live Mashup Pads (beat-locked to the live deck):
 *    - VOCAL STAB: Slices the loudest vocal hook bar from the incoming/other deck (mid-band isolated)
 *      and stabs it rhythmically over the live track's beat grid!
 *    - DRUM CHUNK: Slices the peak percussive bar from the incoming/other deck and overlays a
 *      syncopated 1/2 + 1/4 beat stutter chunk.
 *
 * 3. AUTO SOUNDBOARD Mode:
 *    - When AUTO FX is ON, Jev automatically fires tasteful, phrase-aligned soundboard hits and
 *      smart chunks from the incoming track (4 bars before a mix, on big drops, and during transitions)
 *      and logs every move to the AI Panel!
 */

import { api } from "../api";
import { Store } from "../store";
import type { DeckId } from "../types";
import type { Deck } from "./deck";
import type { Engine } from "./engine";

export type SoundPadId =
  | "air_horn"
  | "dub_siren"
  | "log_drum"
  | "whistle"
  | "laser"
  | "sub_drop"
  | "vocal_chunk"
  | "beat_chunk"
  | "live_mashup";

export interface SoundPadMeta {
  id: SoundPadId;
  label: string;
  short: string;
  key: string; // keyboard shortcut 1..9
  kind: "fx" | "chunk";
  color: "amber" | "cyan" | "green" | "yellow";
  tip: string;
}

export const SOUND_PADS: SoundPadMeta[] = [
  { id: "air_horn", label: "AIR HORN", short: "HORN", key: "1", kind: "fx", color: "amber", tip: "Classic triple club air horn [Key 1]" },
  { id: "dub_siren", label: "DUB SIREN", short: "SIREN", key: "2", kind: "fx", color: "cyan", tip: "Sound-system LFO dub siren [Key 2]" },
  { id: "log_drum", label: "LOG DRUM", short: "LOG", key: "3", kind: "fx", color: "amber", tip: "SA Amapiano log drum bass knock [Key 3]" },
  { id: "whistle", label: "WHISTLE", short: "WHSTL", key: "4", kind: "fx", color: "yellow", tip: "SA festival hype whistle trill [Key 4]" },
  { id: "laser", label: "LASER ZAP", short: "LASER", key: "5", kind: "fx", color: "cyan", tip: "Peak-hour dub laser zap [Key 5]" },
  { id: "sub_drop", label: "808 DROP", short: "808", key: "6", kind: "fx", color: "amber", tip: "Deep 808 sub-bass impact drop [Key 6]" },
  { id: "vocal_chunk", label: "VOCAL CHUNK", short: "VOX", key: "7", kind: "chunk", color: "green", tip: "Smart Chunk: slices the best vocal hook from the other deck on-beat [Key 7]" },
  { id: "beat_chunk", label: "BEAT MASH", short: "BEAT", key: "8", kind: "chunk", color: "green", tip: "Smart Chunk: stutters the peak drum bar from the other deck on-beat [Key 8]" },
  { id: "live_mashup", label: "4-BAR MASHUP", short: "MASH", key: "9", kind: "chunk", color: "green", tip: "Smart Mashup: overlays a 4-bar vocal acapella from the other deck over the live instrumental [Key 9]" },
];

export interface SoundboardState {
  autoEnabled: boolean;
  activePad: SoundPadId | null;
  lastTriggered: { id: SoundPadId; label: string; source: "user" | "jev"; ts: number } | null;
}

export class Soundboard {
  readonly store = new Store<SoundboardState>({
    autoEnabled: true,
    activePad: null,
    lastTriggered: null,
  });

  private fxBus: GainNode;
  private delay: DelayNode;
  private delayFb: GainNode;
  private delayWet: GainNode;
  private clearTimer: number | null = null;
  private lastAutoAt = 0;

  constructor(private engine: Engine, private decks: Record<DeckId, Deck>) {
    const ctx = engine.ctx;
    this.fxBus = new GainNode(ctx, { gain: 0.78 });
    this.delay = new DelayNode(ctx, { maxDelayTime: 2.0, delayTime: 0.375 });
    this.delayFb = new GainNode(ctx, { gain: 0.36 });
    this.delayWet = new GainNode(ctx, { gain: 0.28 });
    const delayHp = new BiquadFilterNode(ctx, { type: "highpass", frequency: 320 });

    this.fxBus.connect(engine.master);
    this.fxBus.connect(this.delay);
    this.delay.connect(delayHp).connect(this.delayFb).connect(this.delay);
    delayHp.connect(this.delayWet).connect(engine.master);
  }

  get state(): SoundboardState {
    return this.store.get();
  }

  setAuto(on: boolean): void {
    this.store.set({ autoEnabled: on });
  }

  private liveDeck(): Deck | null {
    if (this.decks.A.playing && !this.decks.B.playing) return this.decks.A;
    if (this.decks.B.playing && !this.decks.A.playing) return this.decks.B;
    return this.decks.A.playing ? this.decks.A : this.decks.B.playing ? this.decks.B : null;
  }

  private otherDeck(live: Deck | null): Deck | null {
    if (!live) return this.decks.B.track ? this.decks.B : this.decks.A.track ? this.decks.A : null;
    const other = live.id === "A" ? this.decks.B : this.decks.A;
    return other.buffer && other.track ? other : live.buffer && live.track ? live : null;
  }

  /** Trigger any soundboard pad or smart chunk (quantized to the beat grid when a deck is playing). */
  trigger(id: SoundPadId, source: "user" | "jev" = "user", reason?: string): void {
    void this.engine.resume();
    const meta = SOUND_PADS.find((p) => p.id === id) ?? SOUND_PADS[0];
    const live = this.liveDeck();
    const beat = live?.beatPeriod ? live.beatPeriod / live.rate : 0.5;
    this.delay.delayTime.setTargetAtTime(Math.min(1.5, beat * 0.75), this.engine.now, 0.02);

    // Quantize to the next 1/2 beat if triggered by Jev, or immediate/tight-grid for user
    let at = this.engine.now + 0.015;
    if (live?.playing && live.track) {
      const pos = live.position();
      const sub = live.beatPeriod * (source === "jev" ? 1 : 0.25);
      const nextGrid = live.track.first_beat + Math.ceil((pos - live.track.first_beat) / sub) * sub;
      const gridTime = live.timeAt(nextGrid);
      if (gridTime > this.engine.now && gridTime - this.engine.now < 0.45) {
        at = gridTime;
      }
    }

    switch (id) {
      case "air_horn":
        this.playAirHorn(at, beat);
        break;
      case "dub_siren":
        this.playDubSiren(at, beat);
        break;
      case "log_drum":
        this.playLogDrum(at, beat);
        break;
      case "whistle":
        this.playWhistle(at, beat);
        break;
      case "laser":
        this.playLaser(at, beat);
        break;
      case "sub_drop":
        this.playSubDrop(at, beat);
        break;
      case "vocal_chunk":
        this.playSmartChunk(at, "vocal");
        break;
      case "beat_chunk":
        this.playSmartChunk(at, "beat");
        break;
      case "live_mashup":
        this.playLiveMashup(at);
        break;
    }

    this.store.set({
      activePad: id,
      lastTriggered: { id, label: meta.label, source, ts: Date.now() },
    });
    if (this.clearTimer) window.clearTimeout(this.clearTimer);
    this.clearTimer = window.setTimeout(() => {
      if (this.store.get().activePad === id) this.store.set({ activePad: null });
    }, 950);

    void api.soundboardLog(id, meta.label, source, reason ?? (source === "user" ? "manual soundboard hit" : "auto phrase hype")).catch(() => undefined);
  }

  /**
   * Called by AutoDJ on phrase boundaries / pre-mix windows so Jev performs smart live mashups
   * and soundboard hype without overwhelming the track.
   */
  maybeAutoTrigger(opts: {
    barsToMix: number | null;
    phase: string;
    flair: string;
    style?: string | null;
    moment: "pre_mix" | "drop_hit" | "peak_phrase";
  }): void {
    if (!this.state.autoEnabled || opts.flair === "smooth") return;
    const now = Date.now();
    const minCooldownMs = opts.flair === "turnt" ? 16_000 : 26_000;
    if (now - this.lastAutoAt < minCooldownMs) return;

    const live = this.liveDeck();
    const other = this.otherDeck(live);
    const genre = `${live?.track?.genre ?? ""} ${other?.track?.genre ?? ""}`.toLowerCase();
    const isPiano = /amapiano|piano|afro|3-step|house|gqom/i.test(genre);

    let pick: SoundPadId;
    let reason: string;

    if (opts.moment === "pre_mix") {
      // 4 bars before the transition: tease a smart vocal/beat chunk or 4-bar acapella mashup from the incoming track
      if (other && other !== live && other.buffer) {
        const hasVocals = (other.track?.vocal_bars ?? []).some((v) => v > 0.55);
        pick = hasVocals ? (opts.flair === "turnt" ? "live_mashup" : "vocal_chunk") : "beat_chunk";
        reason = `smart ${pick === "live_mashup" ? "4-bar acapella mashup" : "chunk tease"} from incoming '${other.track?.title ?? "next track"}' (4 bars to mix)`;
      } else if (isPiano) {
        pick = "log_drum";
        reason = "amapiano log drum roll-in before transition";
      } else {
        pick = "dub_siren";
        reason = "dub siren build before transition";
      }
    } else if (opts.moment === "drop_hit") {
      // Right as a high-energy transition lands on the drop
      if (opts.style === "siren_spin" || opts.style === "rewind") {
        pick = "air_horn";
        reason = `club air horn on ${opts.style.replace("_", " ")} drop`;
      } else if (opts.style === "log_drum_swap") {
        pick = "whistle";
        reason = "festival whistle on log drum swap drop";
      } else if (opts.phase === "peak") {
        pick = isPiano ? "whistle" : "air_horn";
        reason = `peak-hour drop hype (${opts.phase})`;
      } else {
        pick = "sub_drop";
        reason = "808 sub impact on the drop";
      }
    } else {
      // Mid-track peak phrase mashup
      if (other && other !== live && other.buffer && Math.random() < 0.65) {
        pick = "vocal_chunk";
        reason = `live vocal mashup chunk from '${other.track?.title ?? "incoming"}'`;
      } else {
        pick = isPiano ? "log_drum" : "laser";
        reason = "phrase boundary energy accent";
      }
    }

    this.lastAutoAt = now;
    this.trigger(pick, "jev", reason);
  }

  // ---------------------------------------------------------------- Synth FX

  /** Classic "Prrr-papa-pa!" triple club air horn with detuned sawtooth harmonics. */
  private playAirHorn(at: number, beat: number): void {
    const step = Math.min(0.14, Math.max(0.09, beat * 0.24));
    const bursts: [number, number][] = [
      [0, step * 0.82],
      [step, step * 0.82],
      [step * 2, step * 3.8],
    ];
    const freqs = [480, 605, 720, 960]; // horn chord cluster

    for (const [offset, dur] of bursts) {
      const t0 = at + offset;
      const g = new GainNode(this.engine.ctx, { gain: 0 });
      const bp = new BiquadFilterNode(this.engine.ctx, { type: "bandpass", frequency: 1350, Q: 1.4 });
      bp.connect(g).connect(this.fxBus);

      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.32, t0 + 0.012);
      g.gain.setValueAtTime(0.32, t0 + dur * 0.78);
      g.gain.exponentialRampToValueAtTime(0.001, t0 + dur);

      for (const f of freqs) {
        const osc = new OscillatorNode(this.engine.ctx, { type: "sawtooth", frequency: f * 0.94 });
        osc.frequency.setValueAtTime(f * 0.94, t0);
        osc.frequency.exponentialRampToValueAtTime(f, t0 + 0.028);
        osc.connect(bp);
        osc.start(t0);
        osc.stop(t0 + dur + 0.02);
        osc.onended = () => osc.disconnect();
      }
    }
  }

  /** Jamaica / Mzansi sound-system LFO dub siren. */
  private playDubSiren(at: number, beat: number): void {
    const ctx = this.engine.ctx;
    const dur = Math.min(2.2, Math.max(1.2, beat * 3));
    const carrier = new OscillatorNode(ctx, { type: "square", frequency: 660 });
    const lfo = new OscillatorNode(ctx, { type: "triangle", frequency: 4.5 / Math.max(0.35, beat) });
    const lfoGain = new GainNode(ctx, { gain: 240 });
    const bp = new BiquadFilterNode(ctx, { type: "bandpass", frequency: 1100, Q: 2.2 });
    const env = new GainNode(ctx, { gain: 0 });

    lfo.connect(lfoGain).connect(carrier.frequency);
    carrier.connect(bp).connect(env).connect(this.fxBus);

    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(0.26, at + 0.03);
    env.gain.setValueAtTime(0.26, at + dur * 0.75);
    env.gain.exponentialRampToValueAtTime(0.001, at + dur);

    lfo.start(at);
    carrier.start(at);
    lfo.stop(at + dur + 0.02);
    carrier.stop(at + dur + 0.02);
    carrier.onended = () => {
      lfo.disconnect();
      carrier.disconnect();
      env.disconnect();
    };
  }

  /** Signature SA Amapiano log drum syncopated pattern (woody FM knock + sub body). */
  private playLogDrum(at: number, beat: number): void {
    const ctx = this.engine.ctx;
    // Syncopated 1-bar Amapiano log drum fill (in beats)
    const pattern: [number, number, number][] = [
      [0, 110, 0.42],
      [0.75, 110, 0.38],
      [1.5, 130.8, 0.44],
      [2.0, 98.0, 0.46],
      [2.75, 82.4, 0.50],
    ];
    for (const [bOff, pitch, vel] of pattern) {
      const t0 = at + bOff * beat;
      const dur = Math.min(0.38, beat * 0.68);
      const sub = new OscillatorNode(ctx, { type: "sine", frequency: pitch * 2.6 });
      const wood = new OscillatorNode(ctx, { type: "triangle", frequency: pitch * 4.2 });
      const woodGain = new GainNode(ctx, { gain: 0 });
      const env = new GainNode(ctx, { gain: 0 });

      // Pitch drop gives that signature woody Amapiano log-drum knock
      sub.frequency.setValueAtTime(pitch * 2.6, t0);
      sub.frequency.exponentialRampToValueAtTime(pitch, t0 + 0.042);
      sub.frequency.exponentialRampToValueAtTime(pitch * 0.72, t0 + dur);

      wood.frequency.setValueAtTime(pitch * 4.2, t0);
      wood.frequency.exponentialRampToValueAtTime(pitch * 1.5, t0 + 0.035);

      woodGain.gain.setValueAtTime(vel * 0.55, t0);
      woodGain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.06);

      env.gain.setValueAtTime(0, t0);
      env.gain.linearRampToValueAtTime(vel, t0 + 0.006);
      env.gain.exponentialRampToValueAtTime(0.001, t0 + dur);

      sub.connect(env);
      wood.connect(woodGain).connect(env);
      env.connect(this.engine.master); // direct to master for clean sub punch

      sub.start(t0);
      wood.start(t0);
      sub.stop(t0 + dur + 0.02);
      wood.stop(t0 + dur + 0.02);
      sub.onended = () => {
        sub.disconnect();
        wood.disconnect();
        env.disconnect();
      };
    }
  }

  /** SA Kasi / Amapiano whistle trill (high sine + rapid FM pea trill). */
  private playWhistle(at: number, beat: number): void {
    const ctx = this.engine.ctx;
    const dur = Math.min(1.1, Math.max(0.65, beat * 1.5));
    const osc = new OscillatorNode(ctx, { type: "sine", frequency: 2750 });
    const trill = new OscillatorNode(ctx, { type: "sine", frequency: 28 });
    const trillGain = new GainNode(ctx, { gain: 95 });
    const env = new GainNode(ctx, { gain: 0 });

    osc.frequency.setValueAtTime(2600, at);
    osc.frequency.linearRampToValueAtTime(2920, at + dur * 0.25);
    osc.frequency.setValueAtTime(2860, at + dur * 0.8);
    osc.frequency.linearRampToValueAtTime(2400, at + dur);

    trill.connect(trillGain).connect(osc.frequency);
    osc.connect(env).connect(this.fxBus);

    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(0.22, at + 0.025);
    env.gain.setValueAtTime(0.22, at + dur * 0.8);
    env.gain.exponentialRampToValueAtTime(0.001, at + dur);

    trill.start(at);
    osc.start(at);
    trill.stop(at + dur + 0.02);
    osc.stop(at + dur + 0.02);
    osc.onended = () => {
      trill.disconnect();
      osc.disconnect();
      env.disconnect();
    };
  }

  /** Fast downward FM synth laser zap (3 rapid stabs). */
  private playLaser(at: number, beat: number): void {
    const ctx = this.engine.ctx;
    const step = beat * 0.25;
    for (let i = 0; i < 3; i++) {
      const t0 = at + i * step;
      const dur = Math.min(0.24, step * 0.9);
      const osc = new OscillatorNode(ctx, { type: "sawtooth", frequency: 3400 });
      const lp = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 4500, Q: 4.0 });
      const env = new GainNode(ctx, { gain: 0 });

      osc.frequency.setValueAtTime(3400 - i * 300, t0);
      osc.frequency.exponentialRampToValueAtTime(110, t0 + dur);

      env.gain.setValueAtTime(0, t0);
      env.gain.linearRampToValueAtTime(0.26, t0 + 0.005);
      env.gain.exponentialRampToValueAtTime(0.001, t0 + dur);

      osc.connect(lp).connect(env).connect(this.fxBus);
      osc.start(t0);
      osc.stop(t0 + dur + 0.02);
      osc.onended = () => {
        osc.disconnect();
        env.disconnect();
      };
    }
  }

  /** Deep 808 sub-bass impact drop. */
  private playSubDrop(at: number, beat: number): void {
    const ctx = this.engine.ctx;
    const dur = Math.min(1.8, Math.max(1.0, beat * 2.5));
    const osc = new OscillatorNode(ctx, { type: "sine", frequency: 120 });
    const env = new GainNode(ctx, { gain: 0 });

    osc.frequency.setValueAtTime(125, at);
    osc.frequency.exponentialRampToValueAtTime(32, at + dur);

    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(0.55, at + 0.015);
    env.gain.exponentialRampToValueAtTime(0.001, at + dur);

    osc.connect(env).connect(this.engine.master);
    osc.start(at);
    osc.stop(at + dur + 0.02);
    osc.onended = () => {
      osc.disconnect();
      env.disconnect();
    };
  }

  // ---------------------------------------------------------------- Smart Chunk / Live Mashup

  /**
   * Slices the best vocal or percussive bar from the other/incoming deck, pitch-matches it to the
   * live deck's BPM, isolates the frequency band (band-pass for vocals, high-pass for percussion),
   * and fires a rhythmic stutter pattern on the beat grid!
   */
  private playSmartChunk(at: number, mode: "vocal" | "beat"): void {
    const live = this.liveDeck();
    const srcDeck = this.otherDeck(live);
    if (!srcDeck?.buffer || !srcDeck.track) {
      // Fallback if no track is loaded yet: fire log drum or dub siren
      const beat = live?.beatPeriod ? live.beatPeriod / live.rate : 0.5;
      if (mode === "vocal") this.playWhistle(at, beat);
      else this.playLogDrum(at, beat);
      return;
    }

    const ctx = this.engine.ctx;
    const track = srcDeck.track;
    const downbeats = track.downbeats?.length ? track.downbeats : [track.first_downbeat || 0];
    const energies = track.energy_bars ?? [];
    const vocals = track.vocal_bars ?? [];

    // Pick the most iconic bar from the incoming/source track
    let bestBar = Math.min(16, Math.max(0, Math.floor(downbeats.length / 3)));
    let bestScore = -1;
    for (let i = 4; i < Math.min(downbeats.length - 2, 96); i++) {
      const e = energies[i] ?? 0.5;
      const v = vocals[i] ?? 0.3;
      const score = mode === "vocal" ? v * 1.4 + e * 0.5 : e * 1.4 - v * 0.3;
      if (score > bestScore) {
        bestScore = score;
        bestBar = i;
      }
    }

    const sliceStart = downbeats[bestBar] ?? track.first_downbeat ?? 0;
    const srcBeat = srcDeck.beatPeriod;
    const rate = live && live.playing && live.track
      ? Math.min(1.16, Math.max(0.84, live.bpm / (track.bpm || 120)))
      : srcDeck.rate;
    const liveBeat = live && live.playing ? live.beatPeriod / live.rate : srcBeat / rate;

    // Band isolation filter so the chunk sits cleanly over the playing track without bass clash
    const filter = new BiquadFilterNode(ctx, {
      type: mode === "vocal" ? "bandpass" : "highpass",
      frequency: mode === "vocal" ? 1150 : 260,
      Q: mode === "vocal" ? 1.1 : 0.8,
    });
    filter.connect(this.fxBus);

    // Rhythmic mashup pattern: [offset in beats, slice offset in beats, duration in beats]
    const pattern: [number, number, number][] = mode === "vocal"
      ? [
          [0, 0, 0.75],
          [0.75, 0, 0.75],
          [1.5, 1.0, 0.5],
          [2.0, 2.0, 1.5],
        ]
      : [
          [0, 0, 0.5],
          [0.5, 0, 0.5],
          [1.0, 0.5, 0.25],
          [1.25, 0.5, 0.25],
          [1.5, 0.5, 0.25],
          [1.75, 0.5, 0.25],
          [2.0, 1.0, 1.0],
        ];

    for (const [bOff, sliceBeatOff, durBeats] of pattern) {
      const t0 = at + bOff * liveBeat;
      const durCtx = durBeats * liveBeat;
      const durTrack = durBeats * srcBeat;
      const offsetTrack = Math.max(0, Math.min(srcDeck.buffer.duration - durTrack - 0.05, sliceStart + sliceBeatOff * srcBeat));

      const src = new AudioBufferSourceNode(ctx, { buffer: srcDeck.buffer, playbackRate: rate });
      const env = new GainNode(ctx, { gain: 0 });
      src.connect(env).connect(filter);

      env.gain.setValueAtTime(0, t0);
      env.gain.linearRampToValueAtTime(0.85, t0 + 0.008);
      env.gain.setValueAtTime(0.85, Math.max(t0 + 0.01, t0 + durCtx - 0.02));
      env.gain.linearRampToValueAtTime(0, t0 + durCtx);

      src.start(t0, offsetTrack, durTrack + 0.04);
      src.onended = () => {
        src.disconnect();
        env.disconnect();
      };
    }
  }

  /**
   * 4-Bar Live Acapella + Instrumental Mashup:
   * Extracts a 4-bar vocal hook phrase from the other/incoming deck, pitch-syncs it to the live
   * deck, isolates its vocal frequency band (320 Hz - 4.2 kHz), and carves a subtle mid-EQ pocket
   * in the live deck so the vocal rides over the live instrumental like a studio mashup!
   */
  private playLiveMashup(at: number): void {
    const live = this.liveDeck();
    const srcDeck = this.otherDeck(live);
    if (!srcDeck?.buffer || !srcDeck.track) {
      const beat = live?.beatPeriod ? live.beatPeriod / live.rate : 0.5;
      this.playDubSiren(at, beat);
      return;
    }

    const ctx = this.engine.ctx;
    const track = srcDeck.track;
    const downbeats = track.downbeats?.length ? track.downbeats : [track.first_downbeat || 0];
    const energies = track.energy_bars ?? [];
    const vocals = track.vocal_bars ?? [];

    // Find the best 4-bar vocal hook window in the source track
    let bestBar = Math.min(16, Math.max(0, Math.floor(downbeats.length / 4)));
    let bestScore = -1;
    for (let i = 4; i < Math.min(downbeats.length - 5, 96); i += 4) {
      const vAvg = ((vocals[i] ?? 0) + (vocals[i + 1] ?? 0) + (vocals[i + 2] ?? 0) + (vocals[i + 3] ?? 0)) / 4;
      const eAvg = ((energies[i] ?? 0) + (energies[i + 1] ?? 0) + (energies[i + 2] ?? 0) + (energies[i + 3] ?? 0)) / 4;
      const score = vAvg * 1.6 + eAvg * 0.4;
      if (score > bestScore) {
        bestScore = score;
        bestBar = i;
      }
    }

    const sliceStart = downbeats[bestBar] ?? track.first_downbeat ?? 0;
    const srcBeat = srcDeck.beatPeriod;
    const rate = live && live.playing && live.track
      ? Math.min(1.16, Math.max(0.84, live.bpm / (track.bpm || 120)))
      : srcDeck.rate;
    const liveBar = live && live.playing ? live.barLength() : (4 * srcBeat) / rate;
    const durCtx = 4 * liveBar;
    const durTrack = 16 * srcBeat;

    // Acapella stem isolation chain: high-pass 320 Hz + low-pass 4200 Hz + presence boost at 1.4 kHz
    const hp = new BiquadFilterNode(ctx, { type: "highpass", frequency: 320, Q: 0.85 });
    const lp = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 4200, Q: 0.85 });
    const presence = new BiquadFilterNode(ctx, { type: "peaking", frequency: 1400, Q: 1.0, gain: 3.5 });
    const env = new GainNode(ctx, { gain: 0 });

    const src = new AudioBufferSourceNode(ctx, { buffer: srcDeck.buffer, playbackRate: rate });
    src.connect(hp).connect(lp).connect(presence).connect(env).connect(this.fxBus);

    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(0.92, at + 0.08);
    env.gain.setValueAtTime(0.92, at + durCtx - 0.25);
    env.gain.linearRampToValueAtTime(0, at + durCtx);

    // Carve a temporary vocal pocket (-6.5 dB @ 1 kHz) in the live playing deck so the acapella sits cleanly on top
    if (live && live.playing) {
      const baseMidDb = live.eqMid.gain.value;
      live.eqMid.gain.cancelScheduledValues(at);
      live.eqMid.gain.setValueAtTime(baseMidDb, at);
      live.eqMid.gain.linearRampToValueAtTime(baseMidDb - 6.5, at + 0.12);
      live.eqMid.gain.setValueAtTime(baseMidDb - 6.5, at + durCtx - 0.25);
      live.eqMid.gain.linearRampToValueAtTime(baseMidDb, at + durCtx);
    }

    src.start(at, Math.max(0, Math.min(srcDeck.buffer.duration - durTrack - 0.05, sliceStart)), durTrack + 0.05);
    src.onended = () => {
      src.disconnect();
      hp.disconnect();
      lp.disconnect();
      presence.disconnect();
      env.disconnect();
    };
  }
}
