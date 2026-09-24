/**
 * Live beat lock: the DJ's ears.
 *
 * An AudioWorklet listens to each deck's low end (kicks) and timestamps every hit with sample
 * accuracy. We compare where the kicks actually land against each deck's beat grid. While two
 * decks play together, any drift between them is corrected by briefly nudging the follower's
 * pitch, exactly like a DJ riding the pitch fader. It also learns grid errors per track and
 * saves the fix so the next mix of that track starts tighter.
 */

import { api } from "../api";
import { Store } from "../store";
import type { DeckId } from "../types";
import type { Deck } from "./deck";
import type { Engine } from "./engine";

const WORKLET = `
class KickTap extends AudioWorkletProcessor {
  constructor() { super(); this.avg = 1e-5; this.prev = 0; this.last = -1; this.sub = 32; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i += this.sub) {
      let e = 0;
      const n = Math.min(this.sub, ch.length - i);
      for (let j = i; j < i + n; j++) e += ch[j] * ch[j];
      e /= n;
      const t = (currentFrame + i) / sampleRate;
      if (e > this.avg * 5 && e > 2e-5 && e > this.prev && t - this.last > 0.15) {
        this.port.postMessage(t);
        this.last = t;
      }
      this.avg = this.avg * 0.996 + e * 0.004;
      this.prev = e;
    }
    return true;
  }
}
registerProcessor("jevdj-kick-tap", KickTap);
`;

interface Hit {
  t: number; // context time
  err: number; // seconds (context time) the kick is late (+) or early (-) versus the grid
}

export interface LockState {
  active: boolean;
  errorMs: number | null; // follower minus leader, ms
  corrections: number;
  follower: DeckId | null;
}

export class BeatLock {
  readonly store = new Store<LockState>({ active: false, errorMs: null, corrections: 0, follower: null });
  private hits: Record<DeckId, Hit[]> = { A: [], B: [] };
  private learned: Record<DeckId, { trackId: number; errs: number[] }> = {
    A: { trackId: -1, errs: [] }, B: { trackId: -1, errs: [] },
  };
  private nudgeUntil = 0;
  private baseRate: number | null = null;
  private timer: number | null = null;
  enabled = true;
  leader: () => DeckId | null = () => null;

  constructor(private engine: Engine, private decks: Record<DeckId, Deck>) {}

  private starting: Promise<void> | null = null;

  start(): Promise<void> {
    return (this.starting ??= this.init());
  }

  private async init(): Promise<void> {
    const ctx = this.engine.ctx;
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    for (const id of ["A", "B"] as DeckId[]) {
      const node = new AudioWorkletNode(ctx, "jevdj-kick-tap", { numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1, channelCountMode: "explicit" });
      node.port.onmessage = (e: MessageEvent<number>) => this.onHit(id, e.data);
      this.decks[id].tap.connect(node);
    }
    this.timer = window.setInterval(() => this.tick(), 250);
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }

  private onHit(id: DeckId, t: number): void {
    const deck = this.decks[id];
    const track = deck.track;
    if (!track || !deck.playing || deck.state.loop || deck.state.fx.roll) return;
    const pos = deck.position(t);
    const x = (pos - track.first_beat) / deck.beatPeriod;
    const e = x - Math.round(x);
    if (Math.abs(e) > 0.2) return; // offbeat hit (log drum, snare): not a kick on the grid
    const err = (e * deck.beatPeriod) / deck.rate;
    const list = this.hits[id];
    list.push({ t, err });
    while (list.length && list[0].t < t - 6) list.shift();

    // Learn this track's grid offset (track seconds) across the whole play.
    const l = this.learned[id];
    if (l.trackId !== track.id) this.flushLearned(id, track.id);
    l.errs.push(e * deck.beatPeriod);
  }

  /** When a deck changes track, save what we learned about the previous one's grid. */
  private flushLearned(id: DeckId, nextTrackId: number): void {
    const l = this.learned[id];
    if (l.trackId >= 0 && l.errs.length >= 24) {
      const off = median(l.errs);
      if (Math.abs(off) >= 0.005 && Math.abs(off) <= 0.04) {
        void api.grid(l.trackId, off, "beatlock").then((t) => {
          for (const d of Object.values(this.decks)) d.updateTrack(t);
        }).catch(() => undefined);
      }
    }
    this.learned[id] = { trackId: nextTrackId, errs: [] };
  }

  private offset(id: DeckId, since: number): number | null {
    const recent = this.hits[id].filter((h) => h.t >= since);
    return recent.length >= 4 ? median(recent.map((h) => h.err)) : null;
  }

  private tick(): void {
    const { A, B } = this.decks;
    const both = A.playing && B.playing && A.track && B.track;
    if (!both || !this.enabled) {
      if (this.store.get().active) this.store.set({ active: false, errorMs: null, follower: null });
      this.baseRate = null;
      return;
    }
    const leadId = this.leader() ?? "A";
    const followId: DeckId = leadId === "A" ? "B" : "A";
    const lead = this.decks[leadId];
    const follow = this.decks[followId];
    const now = this.engine.now;
    // Only lock decks that are tempo-synced (otherwise it is a cut, not a blend).
    if (Math.abs(follow.bpm / lead.bpm - 1) > 0.01 && Math.abs(follow.bpm / (lead.bpm * 2) - 1) > 0.01
        && Math.abs(follow.bpm / (lead.bpm / 2) - 1) > 0.01) {
      this.store.set({ active: false, errorMs: null, follower: null });
      return;
    }
    const lo = this.offset(leadId, now - 4);
    const fo = this.offset(followId, Math.max(now - 4, this.nudgeUntil + 0.3));
    if (lo === null || fo === null) {
      this.store.set({ active: true, follower: followId });
      return;
    }
    // Real gap between the two decks' kicks = where each deck's grid sits relative to the other
    // (grid phase difference now) + how far each deck's kicks land from its own grid.
    const beat = follow.beatPeriod / follow.rate;
    let gridGap = lead.beatPhase(lead.position(now)) - follow.beatPhase(follow.position(now));
    gridGap -= Math.round(gridGap);
    const m = gridGap * beat + (fo - lo); // + means the follower's kicks land late
    this.store.set({ active: true, errorMs: Math.round(m * 1000), follower: followId });
    if (now < this.nudgeUntil || Math.abs(m) < 0.004) return;

    // Ride the pitch: catch up (or ease back) over one beat, capped at 3%, then return to the synced tempo.
    if (this.baseRate === null) this.baseRate = follow.rate;
    const base = this.baseRate;
    const nudge = Math.max(-0.03, Math.min(0.03, m / beat));
    follow.setRate(base * (1 + nudge));
    this.nudgeUntil = now + beat;
    setTimeout(() => {
      if (follow.playing && this.baseRate !== null) follow.setRate(this.baseRate);
    }, beat * 1000);
    this.store.set({ corrections: this.store.get().corrections + 1 });
  }
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
