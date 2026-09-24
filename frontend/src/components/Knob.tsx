import { useRef } from "react";

interface Props {
  label: string;
  value: number;
  min?: number;
  max?: number;
  center?: number;
  color?: string;
  size?: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  /** Automation value (same units) drawn as an inner ring while the auto-mixer moves this control. */
  autoValue?: number | null;
}

/** Rotary knob: drag up/down, shift for fine, double-click to reset. */
export function Knob({ label, value, min = -1, max = 1, center = 0, color = "#ffb000", size = 40, onChange, format, autoValue = null }: Props) {
  const drag = useRef<{ y: number; v: number } | null>(null);
  const frac = (value - min) / (max - min);
  const angle = -135 + frac * 270;
  const r = size / 2 - 4;
  const c = size / 2;
  const arc = (from: number, to: number) => {
    const p = (a: number) => [c + r * Math.sin((a * Math.PI) / 180), c - r * Math.cos((a * Math.PI) / 180)];
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M ${x1} ${y1} A ${r} ${r} 0 ${Math.abs(to - from) > 180 ? 1 : 0} ${to > from ? 1 : 0} ${x2} ${y2}`;
  };
  const centerAngle = -135 + ((center - min) / (max - min)) * 270;
  const autoAngle = autoValue == null ? null : -135 + ((Math.min(max, Math.max(min, autoValue)) - min) / (max - min)) * 270;
  const innerArc = (from: number, to: number) => {
    const ri = r - 3.5;
    const p = (a: number) => [c + ri * Math.sin((a * Math.PI) / 180), c - ri * Math.cos((a * Math.PI) / 180)];
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M ${x1} ${y1} A ${ri} ${ri} 0 ${Math.abs(to - from) > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  };

  return (
    <div
      className="knob"
      title={`${label} (double-click to reset)`}
      onPointerDown={(e) => {
        (e.target as Element).setPointerCapture(e.pointerId);
        drag.current = { y: e.clientY, v: value };
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        const range = (max - min) * (e.shiftKey ? 0.001 : 0.006);
        onChange(Math.min(max, Math.max(min, drag.current.v + (drag.current.y - e.clientY) * range)));
      }}
      onPointerUp={() => (drag.current = null)}
      onDoubleClick={() => onChange(center)}
      onWheel={(e) => onChange(Math.min(max, Math.max(min, value - Math.sign(e.deltaY) * (max - min) * 0.02)))}
    >
      <svg width={size} height={size}>
        <path d={arc(-135, 135)} stroke="#262b33" strokeWidth={3} fill="none" />
        {Math.abs(angle - centerAngle) > 0.5 && (
          <path d={arc(Math.min(centerAngle, angle), Math.max(centerAngle, angle))} stroke={color} strokeWidth={3} fill="none" />
        )}
        {autoAngle !== null && Math.abs(autoAngle - centerAngle) > 1 && (
          <path className="knob-auto" d={innerArc(Math.min(centerAngle, autoAngle), Math.max(centerAngle, autoAngle))}
            stroke="#ffe14d" strokeWidth={2.5} fill="none" />
        )}
        <circle cx={c} cy={c} r={r - 5} fill="#14181e" stroke={autoValue != null ? "#ffe14d66" : "#2c323b"} />
        <line
          x1={c} y1={c} x2={c + (r - 7) * Math.sin((angle * Math.PI) / 180)} y2={c - (r - 7) * Math.cos((angle * Math.PI) / 180)}
          stroke="#e8edf2" strokeWidth={2} strokeLinecap="round"
        />
      </svg>
      <div className="knob-label">{label}</div>
      {format && <div className="knob-value mono">{format(value)}</div>}
    </div>
  );
}
