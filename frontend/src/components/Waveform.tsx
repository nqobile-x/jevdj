import { useEffect, useRef } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.esm.js";
import type { Deck } from "../audio/deck";
import { useStore } from "../store";

interface Props {
  deck: Deck;
  color: string;
  zoomed?: boolean;
  mixOut?: number | null;
  mixIn?: number | null;
}

/**
 * wavesurfer renders pre-computed peaks; playback is ours (Web Audio), so we drive the
 * playhead from the deck clock every animation frame. The zoomed view scrolls with a centred
 * playhead and a beat grid, like Virtual DJ.
 */
export function Waveform({ deck, color, zoomed = false, mixOut = null, mixIn = null }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const ws = useRef<WaveSurfer | null>(null);
  const regions = useRef<InstanceType<typeof RegionsPlugin> | null>(null);
  const st = useStore(deck.store);
  const pxPerSec = zoomed ? 90 : 0;

  useEffect(() => {
    if (!el.current || !st.peaks || !st.duration) return;
    const reg = RegionsPlugin.create();
    const w = WaveSurfer.create({
      container: el.current,
      peaks: st.peaks,
      duration: st.duration,
      height: zoomed ? "auto" : 28, // zoomed view stretches with the deck when panels are resized
      waveColor: zoomed ? color : `${color}99`,
      progressColor: zoomed ? `${color}66` : `${color}44`,
      cursorColor: zoomed ? "#ffffff" : "#ffffffcc",
      cursorWidth: zoomed ? 2 : 1,
      barWidth: zoomed ? 2 : 1,
      barGap: zoomed ? 1 : 0,
      normalize: true,
      minPxPerSec: pxPerSec,
      autoScroll: false,
      autoCenter: false,
      hideScrollbar: true,
      interact: true,
      dragToSeek: !zoomed,
      plugins: [reg],
    });
    w.on("interaction", (t) => deck.seek(t));
    ws.current = w;
    regions.current = reg;

    const t = st.track;
    if (t && zoomed) {
      const bar = 4 * (t.beat_period || 60 / t.bpm);
      for (let i = 0, s = t.first_downbeat; s < st.duration; i++, s += bar) {
        reg.addRegion({ start: s, color: i % 8 === 0 ? "#ffffff66" : "#ffffff1f", drag: false, resize: false });
      }
    }
    if (t) {
      reg.addRegion({ start: t.intro_end, color: "#39ff88", drag: false, resize: false, content: zoomed ? "IN" : undefined });
      reg.addRegion({ start: t.outro_start, color: "#ff3b6b", drag: false, resize: false, content: zoomed ? "OUT" : undefined });
    }
    return () => {
      w.destroy();
      ws.current = null;
      regions.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.loadedAt, st.peaks, zoomed, color]);

  // Planned mix points.
  useEffect(() => {
    const reg = regions.current;
    if (!reg) return;
    const marks: ReturnType<InstanceType<typeof RegionsPlugin>["addRegion"]>[] = [];
    if (mixOut != null) marks.push(reg.addRegion({ start: mixOut, color: "#ffe14d", drag: false, resize: false, content: zoomed ? "MIX" : undefined }));
    if (mixIn != null) marks.push(reg.addRegion({ start: mixIn, color: "#ffe14d", drag: false, resize: false }));
    return () => marks.forEach((m) => m.remove());
  }, [mixOut, mixIn, zoomed, st.loadedAt]);

  // Follow the deck clock.
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const w = ws.current;
      if (w && st.duration) {
        const pos = deck.position();
        w.setTime(pos);
        if (zoomed && el.current) w.setScroll(Math.max(0, pos * pxPerSec - el.current.clientWidth / 2));
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [deck, zoomed, pxPerSec, st.duration]);

  return (
    <div className={`wave ${zoomed ? "wave-zoom" : "wave-overview"}`}>
      {zoomed && <div className="wave-center" />}
      <div ref={el} className="wave-inner" />
      {!st.track && <div className="wave-empty">{st.loading ? "LOADING..." : zoomed ? "DROP A TRACK HERE" : ""}</div>}
    </div>
  );
}
