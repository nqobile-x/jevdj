/**
 * Auto mode: asks the backend brain for the next track and transition, preloads the idle deck,
 * schedules the beat-aligned transition, then repeats forever. Handles user overrides
 * (veto, pick another, mix now, manual next) and the optional DJ voice.
 */

import { api } from "../api";
import { Store } from "../store";
import type { DeckId, Flair, MixStyle, NextResponse, TrackSummary, TransitionPlan, TransitionStyle, Vibe, VoiceLine } from "../types";
import type { Deck } from "./deck";
import type { Engine } from "./engine";
import { prelisten, type PrelistenReport } from "./prelisten";
import { STYLE_BARS, runPlan, scheduleTransition, syncRate, type ScheduledTransition } from "./transitions";

type Status = "off" | "starting" | "choosing" | "loading" | "ready" | "mixing" | "error";

export interface AutoState {
  enabled: boolean;
  status: Status;
  message: string;
  live: DeckId | null;
  next: NextResponse | null;
  nextManual: boolean;
  plan: TransitionPlan | null;
  scheduled: ScheduledTransition | null;
  barsToMix: number | null;
  mixProgress: number | null; // 0..1 while mixing
  vibe: Vibe;
  mixStyle: MixStyle;
  flair: Flair;
  manualMix: boolean; // a MIX button transition is running (auto off)
  lastMix: { style: TransitionStyle; into: string; rated: 1 | -1 | null } | null;
  editArmed: { at_s: number; to_s: number } | null;
  ears: { status: "listening" | "done" | "skipped"; report?: PrelistenReport; switchedFrom?: string | null } | null;
  voiceOn: boolean;
  voiceLine: VoiceLine | null;
  lastVoice: VoiceLine | null;
  history: number[];
}

const LOOKAHEAD_S = 4; // schedule the transition this far ahead of the mix point
const TICK_MS = 100;

export class AutoDJ {
  readonly store = new Store<AutoState>({
    enabled: false, status: "off", message: "", live: null, next: null, nextManual: false, plan: null,
    scheduled: null, barsToMix: null, mixProgress: null, vibe: "auto", mixStyle: "club", flair: "creative", manualMix: false, lastMix: null, editArmed: null, ears: null, voiceOn: true, voiceLine: null,
    lastVoice: null, history: [],
  });
  private timer: number | null = null;
  private busy = false;
  private excluded: number[] = [];
  private tracksSinceVoice = 0;
  private voiceEvery = 4 + Math.floor(Math.random() * 3);
  private generation = 0; // bumps on every re-plan so stale async results are ignored
  private ramping = false; // outgoing deck is being ramped toward the incoming tempo
  private startedAt: Record<DeckId, number> = { A: 0, B: 0 }; // where each deck's track started playing

  constructor(private engine: Engine, private decks: Record<DeckId, Deck>) {
    for (const d of Object.values(decks)) d.onEnded = () => this.onDeckEnded(d);
  }

  get s(): AutoState {
    return this.store.get();
  }

  private liveDeck(): Deck | null {
    return this.s.live ? this.decks[this.s.live] : null;
  }

  private idleDeck(): Deck {
    return this.s.live === "A" ? this.decks.B : this.decks.A;
  }

  // ---------------------------------------------------------------- control

  async enable(): Promise<void> {
    await this.engine.resume();
    this.store.set({ enabled: true, status: "starting", message: "" });
    if (this.timer === null) this.timer = window.setInterval(() => this.tick(), TICK_MS);
    try {
      const live = (["A", "B"] as DeckId[]).find((id) => this.decks[id].playing) ?? null;
      if (!live) {
        await api.startSet(this.s.vibe);
        this.store.set({ history: [] });
        await this.startFresh();
      } else {
        this.store.set({ live });
        await this.prepareNext();
      }
    } catch (e) {
      this.fail(e);
    }
  }

  disable(): void {
    this.generation++;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.store.set({ enabled: false, status: "off", next: null, plan: null, barsToMix: null, scheduled: null });
  }

  async setFlair(flair: Flair): Promise<void> {
    this.store.set({ flair });
    await api.setFlair(flair).catch(() => undefined);
    await this.replan();
  }

  /** 🔥 / 👎 on the last transition: Jev learns which moves you like (bounded, resettable). */
  async rate(value: 1 | -1): Promise<void> {
    const last = this.s.lastMix;
    if (!last) return;
    this.store.set({ lastMix: { ...last, rated: value } });
    await api.feedback(value, last.style, `into ${last.into}`).catch(() => undefined);
  }

  async setMixStyle(mixStyle: MixStyle): Promise<void> {
    this.store.set({ mixStyle });
    await api.setMixStyle(mixStyle).catch(() => undefined);
    // Re-plan so the new play length applies to the current track straight away.
    if (this.s.enabled && this.s.status === "ready" && this.s.next && !this.s.nextManual) {
      const live = this.liveDeck();
      if (live?.track) {
        const plan = await api.transition(live.track.id, this.s.next.track.id, this.startedAt[live.id]).catch(() => null);
        if (plan && this.s.status === "ready") {
          this.store.set({ plan });
          void this.earsCheck(this.generation);
        }
      }
    }
  }

  /** MIX button (auto off): blend from the playing deck into the other one now, in the chosen style. */
  manualMix(style: TransitionStyle): void {
    const out = this.decks.A.playing && !this.decks.B.playing ? this.decks.A
      : this.decks.B.playing && !this.decks.A.playing ? this.decks.B : null;
    if (!out?.track || this.s.manualMix || this.s.enabled) return;
    const inc = out === this.decks.A ? this.decks.B : this.decks.A;
    if (!inc.track) return;
    const mixOut = out.nextDownbeat(out.position(this.engine.now + 0.6));
    const mixIn = inc.nextDownbeat(Math.max(0, inc.position() - 0.05));
    const sched = scheduleTransition(this.engine, out, inc, style, STYLE_BARS[style], mixOut, mixIn);
    this.store.set({ manualMix: true, scheduled: sched, mixProgress: 0 });
    const prog = window.setInterval(() => {
      const p = (this.engine.now - sched.t0) / (sched.end - sched.t0);
      this.store.set({ mixProgress: Math.min(1, Math.max(0, p)) });
    }, 100);
    window.setTimeout(() => {
      clearInterval(prog);
      out.stop();
      out.resetAutomation();
      this.store.set({ manualMix: false, scheduled: null, mixProgress: null });
      void api.override("manual_mix", inc.track?.id ?? null, `manual ${style}`).catch(() => undefined);
    }, Math.max(0, (sched.end - this.engine.now) * 1000) + 60);
  }

  async setVibe(vibe: Vibe): Promise<void> {
    this.store.set({ vibe });
    await api.setVibe(vibe).catch(() => undefined);
  }

  setVoice(on: boolean): void {
    this.store.set({ voiceOn: on });
  }

  /** Reject the planned next track and ask again. */
  async veto(action: "veto" | "pick_another" = "veto"): Promise<void> {
    const next = this.s.next;
    if (!next || this.s.status === "mixing") return;
    this.excluded.push(next.track.id);
    await api.override(action, next.track.id, action === "veto" ? "user vetoed this pick" : "user asked for another option");
    await this.prepareNext();
  }

  /** The source changed (library <-> Audius station): re-pick the next track from the new pool. */
  async replan(): Promise<void> {
    if (!this.s.enabled || this.s.status === "mixing" || !this.liveDeck()?.track) return;
    await this.prepareNext();
  }

  /** Spotify-DJ style: take the set in a new direction (genre or energy), announced on the mic. */
  async switchItUp(): Promise<void> {
    if (!this.liveDeck()?.track || this.s.status === "mixing") return;
    if (this.s.next) this.excluded.push(this.s.next.track.id);
    await this.prepareNext(true);
  }

  /** Start the transition on the next downbeat instead of waiting for the outro. */
  async mixNow(): Promise<void> {
    const live = this.liveDeck();
    const plan = this.s.plan;
    if (!live || !plan || this.s.status !== "ready") return;
    const pos = live.position(this.engine.now + 1.2);
    const mixOut = live.nextDownbeat(pos);
    this.store.set({ plan: { ...plan, mix_out_s: mixOut } });
    await api.override("mix_now", live.track?.id ?? null, `mixed early at ${mixOut.toFixed(1)}s`);
    this.startTransition(true);
  }

  /** User dropped a track to play next while auto mode runs. */
  async manualNext(trackId: number): Promise<void> {
    if (this.s.status === "mixing") return;
    const gen = ++this.generation;
    const live = this.liveDeck();
    const inc = this.idleDeck();
    try {
      this.store.set({ status: "loading", message: "loading your pick", nextManual: true });
      await api.override("manual_next", trackId, "user picked the next track");
      const detail = await inc.load(trackId);
      if (gen !== this.generation) return;
      const plan = live?.track ? await api.transition(live.track.id, trackId, this.startedAt[live.id]) : null;
      if (gen !== this.generation) return;
      const next: NextResponse = {
        track: detail, source: "rules", confidence: null, reason: "picked by you", fallback: null,
        decision_ids: [], phase: this.s.next?.phase ?? "build", candidates: 0,
      };
      this.store.set({ next, plan, status: "ready", message: "" });
      void this.earsCheck(gen);
      this.prefetchVoice(detail);
    } catch (e) {
      this.fail(e);
    }
  }

  // ---------------------------------------------------------------- flow

  private async startFresh(): Promise<void> {
    const gen = ++this.generation;
    this.store.set({ status: "choosing", message: "picking an opener" });
    const next = await api.next({ current_id: null, history: this.s.history, vibe: this.s.vibe });
    if (gen !== this.generation) return;
    const deck = this.decks.A.playing ? this.decks.B : this.decks.A;
    this.store.set({ status: "loading", message: `loading ${next.track.title}` });
    const detail = await deck.load(next.track.id);
    if (gen !== this.generation) return;
    deck.resetAutomation();
    deck.play(this.engine.now + 0.1, detail.first_downbeat || 0);
    this.startedAt[deck.id] = detail.first_downbeat || 0;
    this.store.set({ live: deck.id, next });
    if (this.s.voiceOn) {
      // Like Spotify's DJ: say hello over the opening bars.
      api.voiceLine(null, detail.id, "intro").then((line) => {
        const at = this.engine.now + 2 * deck.barLength();
        void speak(this.engine, line, at);
        setTimeout(() => this.store.set({ lastVoice: { ...line } }), 2 * deck.barLength() * 1000);
      }).catch(() => undefined);
    }
    await this.onTrackStarted(deck, null, next.source);
  }

  private async onTrackStarted(deck: Deck, transition: string | null, source: string | null): Promise<void> {
    const id = deck.track!.id;
    this.store.set({ history: [...this.s.history, id].slice(-60) });
    this.excluded = [];
    await api.played(id, transition, source).catch(() => undefined);
    await this.prepareNext();
  }

  private async prepareNext(switchUp = false): Promise<void> {
    const live = this.liveDeck();
    if (!live?.track || !this.s.enabled) return;
    const gen = ++this.generation;
    const inc = this.idleDeck();
    this.ramping = false; // a new plan cancels any tempo ramp in progress
    try {
      this.store.set({
        status: "choosing", message: switchUp ? "switching it up" : "Jev is choosing",
        next: null, plan: null, nextManual: false, voiceLine: null,
      });
      const next = await api.next({
        current_id: live.track.id, history: this.s.history, vibe: this.s.vibe, exclude: this.excluded, switch: switchUp,
      });
      if (gen !== this.generation) return;
      this.store.set({ next, status: "loading", message: "planning transition" });
      const LOAD_LIMIT_MS = 45_000; // a track that won't load in time is skipped: the music must not stall
      const loaded = Promise.race([
        inc.load(next.track.id),
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error("load timeout")), LOAD_LIMIT_MS)),
      ]);
      let plan: TransitionPlan;
      try {
        [plan] = await Promise.all([api.transition(live.track.id, next.track.id, this.startedAt[live.id]), loaded]);
      } catch (e) {
        if (gen !== this.generation) return;
        if ((e as Error).message === "load timeout" || /audio/.test((e as Error).message)) {
          this.excluded.push(next.track.id);
          this.store.set({ message: `couldn't load ${next.track.title}, picking another` });
          void this.prepareNext();
          return;
        }
        throw e;
      }
      if (gen !== this.generation) return;
      inc.setGain(1); // a fresh track starts at unity gain; the pre-listen matches loudness
      this.store.set({ plan, status: "ready", message: "" });
      void this.earsCheck(gen);
      this.prefetchVoice(next.track, switchUp ? "switch" : "next", switchUp);
    } catch (e) {
      if (gen === this.generation) this.fail(e);
    }
  }

  private prefetchVoice(track: TrackSummary, mode: "next" | "switch" = "next", force = false): void {
    if (!this.s.voiceOn || (!force && this.tracksSinceVoice + 1 < this.voiceEvery)) return;
    const live = this.liveDeck();
    api.voiceLine(live?.track?.id ?? null, track.id, mode)
      .then((line) => this.store.set({ voiceLine: line }))
      .catch(() => undefined);
  }

  private tick(): void {
    const live = this.liveDeck();
    const s = this.s;
    if (!s.enabled || !live) return;

    if (s.status === "mixing" && s.scheduled) {
      const p = (this.engine.now - s.scheduled.t0) / (s.scheduled.end - s.scheduled.t0);
      this.store.set({ mixProgress: Math.min(1, Math.max(0, p)), barsToMix: null });
      return;
    }
    if (s.editArmed && live.playing && s.status === "ready") {
      const edit = s.editArmed;
      const pos = live.position();
      const planOut = s.plan?.mix_out_s ?? Infinity;
      if (planOut >= edit.at_s && planOut < edit.to_s) {
        this.store.set({ editArmed: null }); // the next mix-out sits inside the skipped part: keep it
      } else if (pos > edit.at_s) {
        this.store.set({ editArmed: null });
      } else if ((edit.at_s - pos) / live.rate < 2) {
        live.scheduleJump(live.timeAt(edit.at_s), edit.to_s);
        this.store.set({ editArmed: null, message: "edit: skipped the quiet part" });
      }
    }
    if (s.status === "ready" && s.plan && live.playing) {
      const pos = live.position();
      const barLen = live.barLength() * live.rate;
      this.store.set({ barsToMix: Math.max(0, (s.plan.mix_out_s - pos) / barLen) });
      const secondsLeft = (s.plan.mix_out_s - pos) / live.rate;
      const ramp = s.plan.tempo_ramp;
      if (ramp && !this.ramping && secondsLeft <= ramp.bars * live.barLength() + LOOKAHEAD_S + 1) {
        this.startRamp(live, ramp.out_rate, ramp.bars);
      }
      if (secondsLeft <= LOOKAHEAD_S) this.startTransition(secondsLeft < 0.3);
    }
  }

  private startTransition(forceSoon: boolean): void {
    const out = this.liveDeck();
    const inc = this.idleDeck();
    const plan = this.s.plan;
    if (!out?.track || !inc.track || !plan || this.busy) return;
    this.busy = true;
    try {
      let mixOut = plan.mix_out_s;
      if (forceSoon || out.timeAt(mixOut) < this.engine.now + 0.25) {
        // Missed it (tab was busy): take the next downbeat that is still safely in the future.
        mixOut = out.nextDownbeat(out.position(this.engine.now + 0.5));
      }
      const sched = runPlan(this.engine, out, inc, plan, mixOut, !forceSoon);
      this.startedAt[inc.id] = plan.mix_in_s;
      this.store.set({ status: "mixing", scheduled: sched, mixProgress: 0, barsToMix: 0 });
      this.scheduleVoice(sched);
      const waitMs = Math.max(0, (sched.end - this.engine.now) * 1000) + 60;
      window.setTimeout(() => this.finishTransition(out, inc, plan), waitMs);
    } finally {
      this.busy = false;
    }
  }

  private finishTransition(out: Deck, inc: Deck, plan: TransitionPlan): void {
    this.ramping = false;
    this.store.set({
      lastMix: { style: plan.style, into: inc.track?.title ?? "", rated: null },
      ears: null,
      editArmed: plan.edit ? { at_s: plan.edit.at_s, to_s: plan.edit.to_s } : null,
    });
    out.stop();
    out.resetAutomation();
    const source = this.s.nextManual ? "user" : this.s.next?.source ?? null;
    this.store.set({ live: inc.id, scheduled: null, mixProgress: null, status: "choosing" });
    this.easeTempoHome(inc);
    void this.onTrackStarted(inc, plan.style, source);
  }

  /**
   * Jev's ears: pre-listen the planned mix offline, line up the beats, match loudness, and if it
   * still sounds wrong, try alternative plans and keep the best. Runs in the background while the
   * current track plays; if time runs out the mix just goes ahead with the current plan.
   */
  private async earsCheck(gen: number): Promise<void> {
    const out = this.liveDeck();
    const inc = this.idleDeck();
    const plan0 = this.s.plan;
    if (!out?.track || !inc.track || !out.buffer || !inc.buffer || !plan0) return;
    const secondsLeft = () => (this.s.plan ? (this.s.plan.mix_out_s - out.position()) / out.rate : 0);
    if (secondsLeft() < 12) {
      this.store.set({ ears: { status: "skipped" } });
      return;
    }
    this.store.set({ ears: { status: "listening" } });
    const input = (plan: TransitionPlan) => ({
      outBuf: out.buffer!, outTrack: out.track!, outRate: out.rate, inBuf: inc.buffer!, inTrack: inc.track!, plan,
    });
    try {
      let best = await prelisten(input(plan0));
      let switchedFrom: string | null = null;
      if (gen !== this.generation) return;
      if (best.report.score < 6 && secondsLeft() > 25) {
        // Doesn't sound right: ask for other ways to do this mix and listen to those too.
        const alts = await api.alternatives(out.track.id, inc.track.id, this.startedAt[out.id], [plan0.style]).catch(() => []);
        for (const alt of alts) {
          if (gen !== this.generation || secondsLeft() < 12) break;
          const tried = await prelisten(input(alt));
          if (tried.report.score > best.report.score + 0.5) {
            best = tried;
            switchedFrom = plan0.style;
          }
        }
      }
      if (gen !== this.generation || this.s.status !== "ready") return;
      const { plan, report } = best;
      if (report.inGain !== 1) inc.setGain(Math.min(1.5, Math.max(0.3, inc.state.gain * report.inGain)));
      this.store.set({ plan, ears: { status: "done", report, switchedFrom } });
      void api.prelistenLog({
        score: report.score, style: plan.style, issues: report.issues, fixes: report.fixes,
        beat_error_ms: report.beatErrorMs, switched_from: switchedFrom, render_ms: report.renderMs,
      }).catch(() => undefined);
    } catch (e) {
      if (gen === this.generation) this.store.set({ ears: { status: "skipped" } });
      console.warn("pre-listen failed", e);
    }
  }

  /** Tempo bridge: step the outgoing deck toward the meeting tempo, one beat at a time. */
  private startRamp(deck: Deck, target: number, bars: number): void {
    this.ramping = true;
    const start = deck.rate;
    const beats = Math.max(4, bars * 4);
    let i = 0;
    const step = () => {
      if (!this.ramping || deck !== this.liveDeck() || this.s.status !== "ready") return;
      i++;
      deck.setRate(start + ((target - start) * i) / beats);
      if (i < beats) setTimeout(step, (deck.barLength() / 4) * 1000);
    };
    step();
  }

  /** Drift the new track back to its native tempo over ~32 bars so the set doesn't lock to one BPM. */
  private easeTempoHome(deck: Deck): void {
    const step = () => {
      if (deck !== this.liveDeck() || !deck.playing || this.s.status === "mixing" || this.ramping) return;
      const r = deck.rate;
      if (Math.abs(r - 1) < 0.0005) {
        deck.setRate(1);
        return;
      }
      deck.setRate(r + Math.sign(1 - r) * Math.min(0.002, Math.abs(1 - r)));
      setTimeout(step, deck.barLength() * 1000);
    };
    setTimeout(step, deck.barLength() * 4000);
  }

  private scheduleVoice(sched: ScheduledTransition): void {
    const line = this.s.voiceLine;
    this.tracksSinceVoice++;
    if (!line || !this.s.voiceOn) return;
    this.tracksSinceVoice = 0;
    this.voiceEvery = 4 + Math.floor(Math.random() * 3);
    // Speak over the intro of the new track: 2 bars after it comes in.
    const at = Math.max(sched.inStart, sched.t0) + 2 * sched.bar;
    void speak(this.engine, line, at);
    this.store.set({ voiceLine: null });
    // The mascot starts talking when the line actually plays.
    setTimeout(() => this.store.set({ lastVoice: { ...line } }), Math.max(0, (at - this.engine.now) * 1000));
  }

  private onDeckEnded(deck: Deck): void {
    // Safety net: never leave silence. If the live deck ran out without a transition, go now.
    if (!this.s.enabled || deck.id !== this.s.live || this.s.status === "mixing") return;
    const inc = this.idleDeck();
    if (inc.track && this.s.next) {
      inc.setRate(syncRate(deck, inc));
      inc.play(this.engine.now + 0.05, this.s.plan?.mix_in_s ?? inc.track.first_downbeat);
      this.startedAt[inc.id] = this.s.plan?.mix_in_s ?? inc.track.first_downbeat;
      this.store.set({ live: inc.id });
      void this.onTrackStarted(inc, "emergency_cut", this.s.next.source);
    } else {
      void this.startFresh().catch((e) => this.fail(e));
    }
  }

  private fail(e: unknown): void {
    const msg = e instanceof Error ? e.message : String(e);
    this.store.set({ status: "error", message: msg });
    // Retry in a few seconds; the live track keeps playing meanwhile.
    setTimeout(() => {
      if (this.s.enabled && this.s.status === "error") {
        if (this.liveDeck()?.playing) void this.prepareNext();
        else void this.startFresh().catch((err) => this.fail(err));
      }
    }, 5000);
  }
}

/** Play a DJ voice line at context time `at`, ducking the music by 8 dB while it speaks. */
async function speak(engine: Engine, line: VoiceLine, at: number): Promise<void> {
  if (line.audio_url) {
    try {
      const buf = await engine.ctx.decodeAudioData(await (await fetch(`/api${line.audio_url}`)).arrayBuffer());
      const src = new AudioBufferSourceNode(engine.ctx, { buffer: buf });
      src.connect(engine.master);
      const start = Math.max(at, engine.now + 0.05);
      engine.duck(start - 0.25, buf.duration);
      src.start(start);
      return;
    } catch {
      /* fall through to browser speech */
    }
  }
  if (!("speechSynthesis" in window)) return;
  const delay = Math.max(0, (at - engine.now) * 1000);
  setTimeout(() => {
    const u = new SpeechSynthesisUtterance(line.text);
    const voices = speechSynthesis.getVoices();
    u.voice = voices.find((v) => v.lang === "en-ZA") ?? voices.find((v) => v.lang.startsWith("en-GB")) ?? null;
    u.rate = 1.02;
    u.onstart = () => engine.duckOn();
    u.onend = () => engine.duckOff();
    u.onerror = () => engine.duckOff();
    speechSynthesis.speak(u);
  }, delay);
}
