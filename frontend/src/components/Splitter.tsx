import { useEffect, useRef } from "react";

interface Props {
  /** CSS variable on `target` this splitter controls, e.g. "--bottom-h". */
  cssVar: string;
  target: () => HTMLElement | null;
  axis: "y" | "x";
  /** Size from pointer position: px value to store. */
  compute: (e: PointerEvent, el: HTMLElement) => number;
  min: number;
  max: () => number;
  storageKey: string;
}

function load(key: string): number | null {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function save(key: string, v: number | null): void {
  try {
    if (v === null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(Math.round(v)));
  } catch {
    /* storage unavailable: size just isn't remembered */
  }
}

/** Drag handle that resizes a panel (Virtual DJ style). Double-click resets. Size is remembered. */
export function Splitter({ cssVar, target, axis, compute, min, max, storageKey }: Props) {
  const dragging = useRef(false);

  const apply = (px: number | null) => {
    const el = target();
    if (!el) return;
    if (px === null) el.style.removeProperty(cssVar);
    else el.style.setProperty(cssVar, `${Math.max(min, Math.min(max(), px))}px`);
  };

  useEffect(() => {
    apply(load(storageKey));
    const onResize = () => apply(load(storageKey));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className={`splitter splitter-${axis}`}
      title="Drag to resize, double-click to reset"
      onPointerDown={(e) => {
        (e.target as Element).setPointerCapture(e.pointerId);
        dragging.current = true;
        document.body.classList.add("resizing");
      }}
      onPointerMove={(e) => {
        const el = target();
        if (!dragging.current || !el) return;
        const px = Math.max(min, Math.min(max(), compute(e.nativeEvent, el)));
        apply(px);
        save(storageKey, px);
      }}
      onPointerUp={() => {
        dragging.current = false;
        document.body.classList.remove("resizing");
      }}
      onDoubleClick={() => {
        save(storageKey, null);
        apply(null); // back to the layout default
      }}
    />
  );
}
