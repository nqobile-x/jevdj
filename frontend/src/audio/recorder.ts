/**
 * Records the master output to a 24-bit PCM WAV (lossless). An AudioWorklet taps the float
 * signal; samples are packed to 24-bit on the main thread in chunks to keep memory at
 * ~16 MB per minute of stereo 44.1 kHz audio.
 */

import type { Engine } from "./engine";

const WORKLET = `
class TapProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = 8192; this.fill = 0; this.bufs = null; this.on = true;
    this.port.onmessage = (e) => { if (e.data === "stop") { this.flush(); this.on = false; } };
  }
  flush() {
    if (this.bufs && this.fill > 0) {
      const out = this.bufs.map((b) => b.slice(0, this.fill));
      this.port.postMessage(out, out.map((b) => b.buffer));
    }
    this.fill = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!this.on) return false;
    if (!input || input.length === 0) return true;
    if (!this.bufs) this.bufs = [new Float32Array(this.size), new Float32Array(this.size)];
    const n = input[0].length;
    for (let c = 0; c < 2; c++) this.bufs[c].set(input[Math.min(c, input.length - 1)], this.fill);
    this.fill += n;
    if (this.fill + n > this.size) this.flush();
    return true;
  }
}
registerProcessor("jevdj-tap", TapProcessor);
`;

export class MixRecorder {
  private node: AudioWorkletNode | null = null;
  private chunks: Uint8Array[] = [];
  private frames = 0;
  private loaded = false;
  startedAt = 0;

  constructor(private engine: Engine) {}

  get recording(): boolean {
    return this.node !== null;
  }

  get seconds(): number {
    return this.frames / this.engine.ctx.sampleRate;
  }

  get bytes(): number {
    return this.frames * 6;
  }

  async start(): Promise<void> {
    if (this.node) return;
    const ctx = this.engine.ctx;
    if (!this.loaded) {
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      this.loaded = true;
    }
    this.chunks = [];
    this.frames = 0;
    const node = new AudioWorkletNode(ctx, "jevdj-tap", {
      numberOfInputs: 1, numberOfOutputs: 0, channelCount: 2, channelCountMode: "explicit",
    });
    node.port.onmessage = (e: MessageEvent<Float32Array[]>) => this.push(e.data);
    this.engine.output.connect(node);
    this.node = node;
    this.startedAt = Date.now();
  }

  private push([l, r]: Float32Array[]): void {
    const n = l.length;
    const out = new Uint8Array(n * 6);
    let o = 0;
    for (let i = 0; i < n; i++) {
      for (const s of [l[i], r[i]]) {
        const v = Math.max(-1, Math.min(1, s));
        const x = Math.round(v < 0 ? v * 0x800000 : v * 0x7fffff);
        out[o++] = x & 0xff;
        out[o++] = (x >> 8) & 0xff;
        out[o++] = (x >> 16) & 0xff;
      }
    }
    this.chunks.push(out);
    this.frames += n;
  }

  /** Stop and return the WAV file. */
  async stop(): Promise<Blob | null> {
    const node = this.node;
    if (!node) return null;
    await new Promise<void>((resolve) => {
      const prev = node.port.onmessage;
      node.port.onmessage = (e) => {
        prev?.call(node.port, e);
        resolve();
      };
      node.port.postMessage("stop");
      setTimeout(resolve, 300);
    });
    this.engine.output.disconnect(node);
    this.node = null;
    const blob = new Blob([wavHeader(this.frames, this.engine.ctx.sampleRate), ...this.chunks as BlobPart[]], { type: "audio/wav" });
    this.chunks = [];
    return blob;
  }
}

function wavHeader(frames: number, sampleRate: number): ArrayBuffer {
  const channels = 2;
  const bytesPerSample = 3;
  const dataSize = frames * channels * bytesPerSample;
  const buf = new ArrayBuffer(44);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + dataSize, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, channels, true);
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * channels * bytesPerSample, true);
  v.setUint16(32, channels * bytesPerSample, true);
  v.setUint16(34, 24, true);
  str(36, "data");
  v.setUint32(40, dataSize, true);
  return buf;
}
