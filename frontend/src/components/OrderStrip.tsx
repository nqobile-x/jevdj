import type { OrderShape, OrderStatus, TrackSummary } from "../types";

interface Props {
  order: OrderStatus;
  tracks: Map<number, TrackSummary>;
  playingIds: number[];
  onShape: (shape: OrderShape) => void;
}

const SHAPES: { id: OrderShape; label: string; tip: string }[] = [
  { id: "journey", label: "JOURNEY", tip: "Warm up, build to a peak, bring it home" },
  { id: "build", label: "BUILD", tip: "Start easy, climb all the way" },
  { id: "peak", label: "PEAK TIME", tip: "The hottest tracks first, all together" },
];
const SMOOTH = ["rough", "okay", "smooth", "seamless"];

/** The planned set as an energy arc: one bar per track, a dot for how smooth each mix-in is. */
export function OrderStrip({ order, tracks, playingIds, onShape }: Props) {
  const items = order.items;
  const nextId = items.find((i) => !i.played && !playingIds.includes(i.id))?.id;
  return (
    <div className="order-strip">
      <div className="order-head">
        <span className="order-title">SMART ORDER</span>
        <span className="seg">
          {SHAPES.map((s) => (
            <button key={s.id} className={`seg-btn ${order.shape === s.id ? "on" : ""}`} title={s.tip} onClick={() => onShape(s.id)}>
              {s.label}
            </button>
          ))}
        </span>
        <span className="mono dim">
          {items.length} tracks · {order.scope === "audius" ? "Audius station" : "your library"}
          {order.smooth_pct !== null && <> · <b className="ok">{order.smooth_pct}%</b> smooth mixes</>}
        </span>
      </div>
      {items.length === 0 ? (
        <div className="dim order-empty">{order.scope === "audius" ? "Preparing station tracks to arrange..." : "No analysed tracks to arrange yet"}</div>
      ) : (
        <div className="order-bars">
          {items.map((i) => {
            const t = tracks.get(i.id);
            const live = playingIds.includes(i.id);
            const cls = `obar ${i.played && !live ? "played" : ""} ${live ? "live" : ""} ${i.id === nextId ? "next" : ""}`;
            const tip = `${i.pos}. ${t ? `${t.artist} - ${t.title}` : `track ${i.id}`}\n` +
              `${t ? `${Math.round(t.bpm)} BPM · ${t.camelot} · ` : ""}energy ${Math.round(i.energy * 100)}` +
              (i.smooth !== null ? `\nmix in: ${SMOOTH[i.smooth]}` : "");
            return (
              <div key={i.id} className={cls} title={tip}>
                <i style={{ height: `${Math.max(8, i.energy * 100)}%` }} />
                <span className={`sdot s${i.smooth ?? "x"}`} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
