"""CLI: python -m app.analysis.check <file> [more files] - print the analysis for manual verification."""

from __future__ import annotations

import sys
import time

from app.analysis.analyser import analyse_file


def fmt_time(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:05.2f}"


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.analysis.check <audio file> [...]")
        return 2
    for path in argv:
        t0 = time.perf_counter()
        try:
            a = analyse_file(path)
        except Exception as exc:
            print(f"{path}\n  ERROR: {exc}\n")
            continue
        bars = len(a.downbeats)
        print(f"{a.artist + ' - ' if a.artist else ''}{a.title}")
        print(f"  file         {path}")
        print(f"  duration     {fmt_time(a.duration)}  ({bars} bars)")
        print(f"  bpm          {a.bpm:.2f}")
        print(f"  key          {a.key_name}  ->  Camelot {a.camelot}  (conf {a.key_confidence:.2f})")
        print(f"  energy       {a.energy:.2f}")
        print(f"  first beat   {a.first_beat:.3f}s   first downbeat {a.first_downbeat:.3f}s")
        print(f"  intro_end    {fmt_time(a.intro_end)}  ({a.intro_end:.2f}s)")
        print(f"  outro_start  {fmt_time(a.outro_start)}  ({a.outro_start:.2f}s)")
        if a.genre:
            print(f"  genre        {a.genre}")
        print(f"  analysed in  {time.perf_counter() - t0:.1f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
