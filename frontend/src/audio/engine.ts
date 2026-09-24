/**
 * Master audio graph.
 *
 *   deck A ─┐
 *           ├─> musicBus (voice ducking) ─> master ─> safety limiter ─> analyser ─> speakers
 *   deck B ─┘                                  ^                          └─> recorder tap
 *   DJ voice ──────────────────────────────────┘
 *
 * Everything runs in 32-bit float. The limiter only acts above -0.3 dBFS, so a mix at sane levels
 * passes through untouched and lossless sources stay lossless until the output device.
 */

export const DUCK_DB = -8;

export class Engine {
  readonly ctx: BaseAudioContext; // AudioContext live, OfflineAudioContext for pre-listening
  readonly musicBus: GainNode;
  readonly master: GainNode;
  readonly limiter: DynamicsCompressorNode;
  readonly analyser: AnalyserNode;
  readonly output: AudioNode;
  /** Shared reverb (decks send into it) for wash-outs. */
  readonly reverb: ConvolverNode;
  private noise: AudioBuffer;

  constructor(sampleRateOrCtx: number | BaseAudioContext = 44100) {
    this.ctx = typeof sampleRateOrCtx === "number"
      ? new AudioContext({ latencyHint: "playback", sampleRate: sampleRateOrCtx })
      : sampleRateOrCtx;
    this.musicBus = new GainNode(this.ctx, { gain: 1 });
    this.master = new GainNode(this.ctx, { gain: 0.9 });
    this.limiter = new DynamicsCompressorNode(this.ctx, {
      threshold: -0.3, knee: 0, ratio: 20, attack: 0.001, release: 0.08,
    });
    this.analyser = new AnalyserNode(this.ctx, { fftSize: 2048, smoothingTimeConstant: 0.6 });
    this.musicBus.connect(this.master).connect(this.limiter).connect(this.analyser).connect(this.ctx.destination);
    this.output = this.analyser;

    this.reverb = new ConvolverNode(this.ctx, { buffer: impulse(this.ctx, 3.2, 2.2) });
    this.reverb.connect(new GainNode(this.ctx, { gain: 0.7 })).connect(this.musicBus);
    this.noise = noiseBuffer(this.ctx, 2);
  }

  /** Noise riser: band-passed white noise sweeping up over `seconds`, cut dead at the end. */
  riser(at: number, seconds: number, level = 0.22): void {
    const src = new AudioBufferSourceNode(this.ctx, { buffer: this.noise, loop: true });
    const bp = new BiquadFilterNode(this.ctx, { type: "bandpass", frequency: 300, Q: 1.8 });
    const g = new GainNode(this.ctx, { gain: 0 });
    src.connect(bp).connect(g).connect(this.musicBus);
    bp.frequency.setValueAtTime(300, at);
    bp.frequency.exponentialRampToValueAtTime(9000, at + seconds);
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(level, at + seconds * 0.95);
    g.gain.setValueAtTime(level, at + seconds - 0.01);
    g.gain.linearRampToValueAtTime(0, at + seconds);
    src.start(at);
    src.stop(at + seconds + 0.05);
    src.onended = () => src.disconnect();
  }

  async resume(): Promise<void> {
    if (this.ctx instanceof AudioContext && this.ctx.state !== "running") await this.ctx.resume();
  }

  get now(): number {
    return this.ctx.currentTime;
  }

  /** Drop the music by DUCK_DB for `seconds`, starting at `at` (context time). */
  duck(at: number, seconds: number, fade = 0.25): void {
    const g = this.musicBus.gain;
    const low = Math.pow(10, DUCK_DB / 20);
    g.cancelScheduledValues(at);
    g.setValueAtTime(g.value, at);
    g.linearRampToValueAtTime(low, at + fade);
    g.setValueAtTime(low, at + fade + seconds);
    g.linearRampToValueAtTime(1, at + fade * 2 + seconds);
  }

  duckOn(): void {
    const t = this.now;
    this.musicBus.gain.cancelScheduledValues(t);
    this.musicBus.gain.setTargetAtTime(Math.pow(10, DUCK_DB / 20), t, 0.08);
  }

  duckOff(): void {
    const t = this.now;
    this.musicBus.gain.cancelScheduledValues(t);
    this.musicBus.gain.setTargetAtTime(1, t, 0.15);
  }

  /** Peak level of the master output, 0..1, for the VU meter. */
  level(buf = new Float32Array(1024)): number {
    this.analyser.getFloatTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i]));
    return peak;
  }
}

/** Synthetic stereo reverb impulse: decaying noise, a bit darker at the tail. */
function impulse(ctx: BaseAudioContext, seconds: number, decay: number): AudioBuffer {
  const len = Math.floor(ctx.sampleRate * seconds);
  const buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    let lp = 0;
    for (let i = 0; i < len; i++) {
      const t = i / len;
      lp += ((Math.random() * 2 - 1) - lp) * (0.9 - 0.7 * t);
      d[i] = lp * Math.pow(1 - t, decay);
    }
  }
  return buf;
}

function noiseBuffer(ctx: BaseAudioContext, seconds: number): AudioBuffer {
  const buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * seconds), ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  return buf;
}
