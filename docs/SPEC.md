# JevDJ - Build Spec

## 1. Goal
Press play once and get a smooth, club-style continuous mix from your own music, where an AI
picks every next track and every transition, and you can watch (and override) its decisions
on a Virtual-DJ style deck interface.

## 2. Architecture

```
 MUSIC_DIR (mp3/wav/flac)
      |
      v
 [Analyser - librosa]  BPM, beat grid, key (Camelot), energy curve, intro/outro, vocal-density
      |  cached in SQLite
      v
 [Brain - FastAPI]  candidate filter (rules) -> Jev questions -> decision + confidence
      |  REST + WebSocket
      v
 [Frontend - React + Web Audio]
   Deck A / Deck B players, tempo sync, 3-band EQ, filters, crossfader automation,
   waveforms, library, AI decision panel, DJ voice ducking
```

## 3. Track analysis (backend/app/analysis/)
Per track, computed once and cached:
- bpm, beat_times (for beat grid), downbeats (first beat of each bar)
- key -> Camelot code (e.g. 8A). Use chroma + Krumhansl key profiles.
- energy: RMS curve per bar, normalised 0-1, plus overall energy score
- intro_end_s and outro_start_s: first/last region with low vocal density and steady beat
  (these are the safe mix-in / mix-out windows)
- vocal_density per bar (spectral flux in 300Hz-3kHz band as a proxy)
- genre tag from file metadata if present
- duration

## 4. How the AI thinks (Jev decision design)
Jev only sees text, so we describe tracks as compact text cards:

`"Kabza - Track X | 113 BPM | 8A | energy 0.72 | vocal-heavy intro | amapiano"`

Pipeline for choosing the next track:
1. Rules pre-filter the library to <= 20 candidates:
   BPM within +-8% (or half/double time), Camelot compatible (same, +-1, or relative major/minor),
   not played in last 30 tracks.
2. Jev **Choice**: "Which track should play next to keep the set flowing?"
   criteria = candidate text cards, plus context: current track card, set phase
   (warm-up / build / peak / cool-down), last 3 tracks, user vibe setting.
3. Jev **Score** on the top 3 by probability: "How smooth will the transition from A to B be?"
   criteria levels: "clashing", "noticeable but okay", "smooth", "seamless". Pick highest.

Transition decisions, asked when the next track is locked:
- Jev **Choice** transition style:
  - "long_blend": 32-bar EQ swap, bass swap at bar 16 (same genre, close BPM)
  - "quick_cut": 4-bar cut on a downbeat (big energy change, or clashing keys)
  - "filter_fade": high-pass sweep out over 16 bars (genre change)
  - "echo_out": echo/delay tail on outgoing track then drop the new one (end of a peak)
- Jev **Noul**: "Is the outgoing track in a vocal section at the planned mix point?"
  If probability > 0.6, shift the mix point to the next instrumental phrase.
- Jev **Score** set phase every 5 tracks: which phase should the set move to next.

Every answer returns type, confidence, probabilities. Log them all to the AI panel.
Fallback: if confidence < 0.55 or API fails, use rules.py (best Camelot + closest BPM + energy step).

## 5. Mixing engine (frontend/src/audio/)
- Two decks, each: AudioBufferSourceNode -> 3-band EQ (BiquadFilter low/mid/high) -> filter
  (HP/LP sweep) -> gain -> crossfader -> master.
- Tempo sync: set playbackRate so incoming BPM matches outgoing (keep within +-8%).
- Beat alignment: start incoming so its downbeat lands on the outgoing downbeat
  (use beat grids from backend, schedule with AudioContext.currentTime).
- Transition runner: executes the chosen style as a timeline of automated param ramps
  (linearRampToValueAtTime) over N bars.
- Voice ducking: when the DJ voice plays, drop music gain by 8 dB for its duration.

## 6. DJ voice (optional)
Every 4-6 tracks, backend asks Groq (llama model) for one line under 20 words:
"Next up: <track>. We're building into the peak." Kokoro turns it into audio, frontend plays it
over the intro of the new track with ducking. Toggle on/off in the UI.

## 7. UI layout (Virtual DJ style)
```
+---------------------------------------------------------------------------+
| JEVDJ     [AUTO: ON]  Vibe: [warm-up|build|peak|cool]   Voice: [on]   BPM  |
+-----------------------------------+---------------------------------------+
| DECK A                            | DECK B                                |
| title / artist        8A  113.0   | title / artist        9A  114.0       |
| [====== scrolling waveform ======]| [====== scrolling waveform ======]    |
| cue  play  sync   pitch fader     | cue  play  sync   pitch fader         |
+---------------+-------------------+-------------------+-------------------+
| EQ  HI MID LOW  FILTER   gain     |        [ ===|=== ] crossfader          |
+---------------------------------------------------------------------------+
| LIBRARY (search, bpm, key, energy)   | AI PANEL                           |
|  track list, drag to deck           |  Next: X (conf 0.87)               |
|                                     |  Why: key 8A->9A, energy +0.1      |
|                                     |  Transition: long_blend (0.74)     |
|                                     |  [Veto] [Pick another] [Mix now]   |
+---------------------------------------------------------------------------+
```
- Dark background, two deck accent colours (amber for A, cyan for B), mono numbers.
- Upcoming transition shown as a countdown in bars.
- User overrides (veto, mix now, drag own track) are logged and fed back as context.

## 8. API (backend)
- POST /library/scan  - analyse MUSIC_DIR (progress over WebSocket)
- GET  /library       - tracks with analysis
- GET  /audio/{id}    - stream the raw file to the frontend
- POST /brain/next    - body: current_id, history, vibe -> next track + reasoning + confidences
- POST /brain/transition - body: from_id, to_id -> style, mix_in_s, mix_out_s, bars
- POST /voice/line    - body: context -> text + wav url
- WS   /events        - decision log stream

## 9. Phases
1. Analyser + SQLite + /library, tested on 20 tracks (check BPM vs a known source).
2. Two-deck player with manual crossfader, EQ, waveforms. No AI yet.
3. Rules-based auto-mix with beat-aligned long_blend. Must sound clean before adding AI.
4. Jev brain: next-track Choice, transition Choice/Score, vocal Noul, AI panel + logging.
5. All 4 transition styles + overrides.
6. Groq + Kokoro DJ voice with ducking.
7. Polish: set history export, "record mix" to WAV via MediaRecorder.

## 10. Done means
- 1 hour unattended mix with no silent gaps, no obvious beat clashes.
- Every AI decision visible with confidence and a one-line reason.
- Works fully offline except Jev/Groq calls; falls back to rules if offline.
