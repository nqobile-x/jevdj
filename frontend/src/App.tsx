import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, connectEvents } from "./api";
import { AutoDJ } from "./audio/autodj";
import { BeatLock } from "./audio/beatlock";
import { Deck } from "./audio/deck";
import { Engine } from "./audio/engine";
import { MixRecorder } from "./audio/recorder";
import { AIPanel } from "./components/AIPanel";
import { DeckView } from "./components/DeckView";
import { Library } from "./components/Library";
import { Mascot, type MascotMood } from "./components/Mascot";
import { Mixer } from "./components/Mixer";
import { Splitter } from "./components/Splitter";
import { TopBar } from "./components/TopBar";
import { useStore } from "./store";
import type { Decision, DeckId, Health, Phase, RadioStatus, ServerEvent, TrackSummary } from "./types";

const AMBER = "#ffb000";
const CYAN = "#00e5ff";

let rigSingleton: ReturnType<typeof createRig> | null = null;

function createRig() {
  const engine = new Engine(44100);
  const decks: Record<DeckId, Deck> = { A: new Deck("A", engine), B: new Deck("B", engine) };
  const auto = new AutoDJ(engine, decks);
  const recorder = new MixRecorder(engine);
  const lock = new BeatLock(engine, decks);
  // The live (outgoing) deck leads; the other one follows its kicks.
  lock.leader = () => auto.store.get().live ?? (decks.A.playing && !decks.B.playing ? "A" : decks.B.playing && !decks.A.playing ? "B" : "A");
  const rig = { engine, decks, auto, recorder, lock };
  if (import.meta.env.DEV) (window as unknown as { __jevdj: typeof rig }).__jevdj = rig; // dev-only test hook
  return rig;
}

export default function App() {
  const rig = useMemo(() => (rigSingleton ??= createRig()), []);
  const { engine, decks, auto, recorder, lock } = rig;
  const autoState = useStore(auto.store);
  const a = useStore(decks.A.store);
  const b = useStore(decks.B.store);

  const [tracks, setTracks] = useState<TrackSummary[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [wsUp, setWsUp] = useState(false);
  const [phase, setPhase] = useState<Phase | null>(null);
  const [scan, setScan] = useState<{ running: boolean; done: number; total: number; current: string } | null>(null);
  const [crossfader, setCrossfader] = useState(0.5);
  const [rec, setRec] = useState({ on: false, s: 0, bytes: 0 });
  const [toast, setToast] = useState<string | null>(null);
  const [autoMatch, setAutoMatch] = useState(true);
  const [upload, setUpload] = useState<number | null>(null);
  const [broken, setBroken] = useState<{ title: string; error: string }[]>([]);
  const [sourceStatus, setSourceStatus] = useState<Record<string, { stage: string; error?: string }>>({});
  const [radio, setRadio] = useState<RadioStatus | null>(null);
  const stationKey = useRef<string>("");
  const toastTimer = useRef<number | null>(null);
  const appRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const loadLibrary = useCallback(() => {
    api.library().then(setTracks).catch(() => undefined);
    api.libraryErrors()
      .then((l) => setBroken(l.map((t) => ({ title: t.title, error: (t as unknown as { error: string }).error }))))
      .catch(() => undefined);
  }, []);

  // Start the ears once audio is allowed (first click), and follow the MATCH toggle.
  useEffect(() => {
    lock.enabled = autoMatch || auto.store.get().enabled;
  }, [autoMatch, lock, auto, autoState.enabled]);

  useEffect(() => {
    loadLibrary();
    api.health().then((h) => {
      setHealth(h);
      setPhase(h.phase);
    }).catch(() => notify("Backend not reachable on :8000 - start it with uvicorn"));
    api.decisions().then(setDecisions).catch(() => undefined);
    return connectEvents((e: ServerEvent) => {
      switch (e.type) {
        case "decision":
          setDecisions((d) => [e.decision, ...d].slice(0, 200));
          break;
        case "scan_started":
          setScan({ running: true, done: 0, total: e.total, current: "" });
          break;
        case "scan_progress":
          setScan({ running: true, done: e.done, total: e.total, current: e.current });
          if (e.done % 5 === 0) loadLibrary();
          break;
        case "scan_finished":
          setScan(null);
          loadLibrary();
          api.health().then(setHealth).catch(() => undefined);
          if (!e.quiet) notify(e.error ? `Scan failed: ${e.error}` : `Scan done: ${e.analysed ?? 0} analysed, ${e.failed ?? 0} failed`);
          break;
        case "phase":
        case "set_started":
          setPhase(e.phase);
          break;
        case "hello":
          setPhase(e.phase);
          break;
        case "source_progress":
          setSourceStatus((s) => ({ ...s, [e.id]: { stage: e.stage, error: e.error } }));
          if (e.stage === "done") notify(`Added from Audius: ${e.title}`);
          if (e.stage === "error") notify(`Audius: could not add ${e.title ?? e.id} - ${e.error}`);
          break;
        case "radio": {
          const { type: _t, ...status } = e;
          void _t;
          setRadio(status);
          break;
        }
        case "library_change":
          if (e.added.length) notify(`New music found: ${e.added.slice(0, 3).join(", ")}${e.added.length > 3 ? ` +${e.added.length - 3} more` : ""}`);
          break;
      }
    }, setWsUp);
  }, [loadLibrary, notify]);

  // Equal-power crossfader.
  useEffect(() => {
    const t = engine.now;
    decks.A.xfade.gain.setTargetAtTime(Math.cos((crossfader * Math.PI) / 2), t, 0.01);
    decks.B.xfade.gain.setTargetAtTime(Math.sin((crossfader * Math.PI) / 2), t, 0.01);
  }, [crossfader, engine, decks]);

  // Recorder clock.
  useEffect(() => {
    if (!rec.on) return;
    const id = setInterval(() => setRec({ on: true, s: recorder.seconds, bytes: recorder.bytes }), 500);
    return () => clearInterval(id);
  }, [rec.on, recorder]);

  // Keyboard: space = play/pause live deck, Q/W = cue A/B.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        void engine.resume();
        (decks[autoState.live ?? "A"]).toggle();
      }
      if (e.key === "q") decks.A.cue();
      if (e.key === "w") decks.B.cue();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [engine, decks, autoState.live]);

  /** AUDIUS tab open = AUTO mixes from that search; LIBRARY tab = your own music. */
  const setStation = async (query: string | null, genre: string | null) => {
    const key = `${query ?? ""}|${genre ?? ""}`;
    if (key === stationKey.current) return;
    stationKey.current = key;
    try {
      setRadio(await api.radioStart(query, genre));
      notify(`AUTO now mixes from Audius: ${query ?? `trending ${genre ?? ""}`}`);
      void auto.replan();
    } catch (e) {
      notify((e as Error).message);
    }
  };
  const onLibraryTab = async (tab: "mine" | "audius") => {
    if (tab === "mine" && radio?.active) {
      stationKey.current = "";
      setRadio(await api.radioStop());
      notify("AUTO now mixes from your library");
      void auto.replan();
    }
  };

  const addFiles = async (files: File[]) => {
    setUpload(0);
    try {
      const r = await api.upload(files, setUpload);
      const bad = r.rejected.length ? ` - skipped ${r.rejected.map((x) => `${x.file} (${x.reason})`).join(", ")}` : "";
      notify(`Added ${r.saved.length} track${r.saved.length === 1 ? "" : "s"}, analysing now${bad}`);
    } catch (e) {
      notify(`Upload failed: ${(e as Error).message}`);
    } finally {
      setUpload(null);
    }
  };

  const loadToDeck = async (deck: Deck, trackId: number) => {
    await engine.resume();
    const liveId = autoState.live;
    if (autoState.enabled) {
      if (deck.id === liveId) return notify("That deck is live - drop onto the other deck to queue it next");
      await auto.manualNext(trackId);
      return;
    }
    try {
      await deck.load(trackId);
      const other = deck === decks.A ? decks.B : decks.A;
      if (autoMatch && other.playing) deck.syncTempo(other); // AUTO MATCH: ready at the master tempo
      await api.override("manual_load", trackId, `loaded on deck ${deck.id}`).catch(() => undefined);
    } catch (e) {
      notify(`Could not load: ${(e as Error).message}`);
    }
  };

  const loadIdle = (trackId: number) => {
    const target = autoState.live ? decks[autoState.live === "A" ? "B" : "A"] : decks.A.playing ? decks.B : decks.A;
    void loadToDeck(target, trackId);
  };

  const toggleAuto = async () => {
    if (autoState.enabled) auto.disable();
    else {
      if (!tracks.length && !radio?.active) return notify("Library is empty - add music or open the AUDIUS tab");
      await auto.enable();
    }
  };

  const toggleRec = async () => {
    await engine.resume();
    if (recorder.recording) {
      const blob = await recorder.stop();
      setRec({ on: false, s: 0, bytes: 0 });
      if (blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `jevdj-mix-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.wav`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      }
    } else {
      await recorder.start();
      setRec({ on: true, s: 0, bytes: 0 });
    }
  };

  const liveDeck = autoState.live ? decks[autoState.live] : a.playing ? decks.A : b.playing ? decks.B : null;
  const liveTrack = liveDeck?.track ?? null;
  const masterBpm = liveDeck?.bpm ?? 0;
  const plan = autoState.plan;
  const liveId = autoState.live;
  const mood: MascotMood =
    autoState.lastVoice && autoState.status !== "off" && speakingRecently(autoState.lastVoice.text) ? "talking"
      : autoState.status === "mixing" ? "mixing"
      : autoState.status === "choosing" || autoState.status === "loading" ? "thinking"
      : a.playing || b.playing ? "idle" : "sleep";

  return (
    <div className="app" ref={appRef} onPointerDown={() => void engine.resume().then(() => lock.start()).catch(() => undefined)}>
      <TopBar
        auto={autoState.enabled} onAuto={toggleAuto}
        vibe={autoState.vibe} phase={phase} onVibe={(v) => void auto.setVibe(v)}
        voice={autoState.voiceOn} onVoice={() => auto.setVoice(!autoState.voiceOn)}
        masterBpm={masterBpm} jevOnline={!!health?.jev_online} wsUp={wsUp}
        recording={rec.on} recSeconds={rec.s} recBytes={rec.bytes} onRecord={toggleRec}
        sampleRate={engine.ctx.sampleRate}
        mixStyle={autoState.mixStyle} onMixStyle={(m) => void auto.setMixStyle(m)}
        autoMatch={autoMatch} onAutoMatch={() => setAutoMatch((v) => !v)}
        flair={autoState.flair} onFlair={(f) => void auto.setFlair(f)}
      />

      <div className="decks">
        <DeckView
          deck={decks.A} other={decks.B} color={AMBER} live={liveId === "A"}
          mixOut={liveId === "A" ? plan?.mix_out_s ?? null : null}
          mixIn={liveId === "B" ? plan?.mix_in_s ?? null : null}
          onDropTrack={(d, id) => void loadToDeck(d, id)}
          autoMatch={autoMatch}
        />
        <Mascot mood={mood} bpm={masterBpm} line={autoState.lastVoice?.text ?? null} crossfader={autoState.live === "B" ? 1 : autoState.live === "A" ? 0 : crossfader} />
        <DeckView
          deck={decks.B} other={decks.A} color={CYAN} live={liveId === "B"}
          mixOut={liveId === "B" ? plan?.mix_out_s ?? null : null}
          mixIn={liveId === "A" ? plan?.mix_in_s ?? null : null}
          onDropTrack={(d, id) => void loadToDeck(d, id)}
          autoMatch={autoMatch}
        />
      </div>

      <Mixer
        a={decks.A} b={decks.B} crossfader={crossfader} onCrossfader={setCrossfader}
        level={() => engine.level()} mixProgress={autoState.mixProgress}
        lock={lock.store}
        canManualMix={!autoState.enabled && !autoState.manualMix && a.playing !== b.playing && !!a.track && !!b.track}
        onManualMix={(s) => auto.manualMix(s)}
      />

      <Splitter
        axis="y" cssVar="--bottom-h" storageKey="jevdj.bottomH" target={() => appRef.current}
        compute={(e, el) => el.getBoundingClientRect().bottom - e.clientY - 4}
        min={120} max={() => window.innerHeight - 200}
      />

      <div className="bottom" ref={bottomRef}>
        <Library
          tracks={tracks}
          playingIds={[a.track?.id, b.track?.id].filter((x): x is number => !!x)}
          refCamelot={liveTrack?.camelot ?? null}
          refBpm={liveDeck?.bpm || null}
          scan={scan}
          musicDir={health?.music_dir ?? ""}
          onScan={(force) => api.scan(force).catch((e) => notify(e.message))}
          onLoad={loadIdle}
          onAddFiles={(f) => void addFiles(f)}
          onOpenFolder={() => void api.openFolder().catch((e) => notify(e.message))}
          upload={upload}
          broken={broken}
          sourceStatus={sourceStatus}
          onError={notify}
          onTab={(t) => void onLibraryTab(t)}
          onStation={(q, g) => void setStation(q, g)}
        />
        <Splitter
          axis="x" cssVar="--lib-w" storageKey="jevdj.libW" target={() => bottomRef.current}
          compute={(e, el) => e.clientX - el.getBoundingClientRect().left - 3}
          min={320} max={() => (bottomRef.current?.getBoundingClientRect().width ?? 1200) - 300}
        />
        <AIPanel
          auto={autoState} decisions={decisions}
          onVeto={() => void auto.veto("veto")}
          onPickAnother={() => void auto.veto("pick_another")}
          onMixNow={() => void auto.mixNow()}
          onSwitch={() => void auto.switchItUp()}
          radio={radio}
          onRate={(v) => void auto.rate(v)}
        />
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

// The mascot "talks" for a few seconds after a voice line is triggered.
const spokenAt = new Map<string, number>();
function speakingRecently(text: string): boolean {
  if (!spokenAt.has(text)) spokenAt.set(text, Date.now());
  return Date.now() - (spokenAt.get(text) ?? 0) < 9000;
}
