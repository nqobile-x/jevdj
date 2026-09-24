import { useEffect, useState } from "react";

export type MascotMood = "sleep" | "idle" | "thinking" | "mixing" | "talking";

interface Props {
  mood: MascotMood;
  bpm: number;
  line: string | null;
  crossfader: number; // 0..1, hands follow it while mixing
}

const CUSTOM = ["/mascot.svg", "/mascot.png", "/mascot.gif", "/mascot.webp"];

/**
 * Jev, the DJ in the booth between the decks. Bobs on the beat, thinks while the brain picks,
 * works the decks during a transition and talks when the DJ voice speaks.
 * Drop your own art at frontend/public/mascot.(svg|png|gif|webp) and it replaces the built-in one.
 */
export function Mascot({ mood, bpm, line, crossfader }: Props) {
  const [custom, setCustom] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      for (const url of CUSTOM) {
        const ok = await new Promise<boolean>((res) => {
          const img = new Image();
          img.onload = () => res(img.naturalWidth > 0);
          img.onerror = () => res(false);
          img.src = url;
        });
        if (ok && alive) return setCustom(url);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const beat = bpm > 0 ? 60 / bpm : 0.5;
  const style = { ["--beat" as string]: `${beat}s`, ["--xf" as string]: `${(crossfader - 0.5) * 24}deg` };

  return (
    <div className={`booth mood-${mood}`} style={style}>
      {line && mood === "talking" && <div className="bubble">{line}</div>}
      {mood === "thinking" && <div className="bubble think"><i /><i /><i /></div>}
      <div className="mascot">
        {custom ? (
          <img src={custom} alt="Jev" className="mascot-img" draggable={false} />
        ) : (
          <svg viewBox="0 0 120 120" className="mascot-svg" aria-label="Jev the DJ">
            <defs>
              <linearGradient id="visor" x1="0" x2="1">
                <stop offset="0" stopColor="#ffb000" />
                <stop offset="1" stopColor="#00e5ff" />
              </linearGradient>
            </defs>
            {/* headphone band */}
            <path d="M22 58 C22 18, 98 18, 98 58" fill="none" stroke="#2a3038" strokeWidth="7" strokeLinecap="round" />
            {/* head */}
            <rect x="30" y="30" width="60" height="56" rx="18" fill="#161b22" stroke="#39414c" strokeWidth="2" />
            {/* visor */}
            <rect x="37" y="46" width="46" height="20" rx="10" fill="#07090c" stroke="url(#visor)" strokeWidth="2" />
            <g className="eyes">
              <circle className="eye" cx="50" cy="56" r="4" fill="#ffb000" />
              <circle className="eye" cx="70" cy="56" r="4" fill="#00e5ff" />
            </g>
            {/* mouth */}
            <rect className="mouth" x="52" y="73" width="16" height="3" rx="1.5" fill="#e8edf2" />
            {/* ear cups */}
            <rect x="14" y="48" width="14" height="24" rx="6" fill="#ffb000" />
            <rect x="92" y="48" width="14" height="24" rx="6" fill="#00e5ff" />
            {/* antenna */}
            <line x1="60" y1="30" x2="60" y2="20" stroke="#39414c" strokeWidth="2" />
            <circle className="antenna" cx="60" cy="17" r="3.5" fill="#39ff88" />
          </svg>
        )}
      </div>
      <div className="hands">
        <span className="hand left" />
        <span className="hand right" />
      </div>
      <div className="booth-desk">
        <span className="jog a" />
        <span className="booth-name">JEV</span>
        <span className="jog b" />
      </div>
    </div>
  );
}
