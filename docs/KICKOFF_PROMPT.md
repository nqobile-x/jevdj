# Claude Code kickoff

## 0. Setup (once)
```
mkdir jevdj && cd jevdj
# copy CLAUDE.md, .env.example, .gitignore, docs/ into this folder
git init
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
cp .env.example .env   # then add your TYPESAFE_API_KEY and GROQ_API_KEY
claude
```

## Phase prompts (paste one at a time, test before moving on)

**Phase 1**
Read CLAUDE.md and docs/SPEC.md. Build Phase 1: the Python backend skeleton (FastAPI) and the
librosa analyser in section 3, cached in SQLite, with POST /library/scan and GET /library.
Add a small CLI `python -m app.analysis.check <file>` that prints BPM, Camelot key, energy,
intro_end and outro_start so I can verify against tracks I know. Write tests for key-to-Camelot.

**Phase 2**
Build Phase 2: Vite + React + TS frontend with the Virtual DJ layout from SPEC section 7.
Two decks with wavesurfer waveforms, play/cue/sync, pitch, 3-band EQ, filter, crossfader, and the
library list loading from GET /library with drag-to-deck. Follow the UI rules in CLAUDE.md.

**Phase 3**
Build Phase 3: rules.py next-track picker and a beat-aligned long_blend transition runner in
src/audio. Auto mode plays forever. Log each decision to the AI panel as "rules".

**Phase 4**
Use the typesafe skill. Build Phase 4: brain/jev.py implementing the Choice, Score and Noul
questions exactly as SPEC section 4. Track text cards, confidence fallback to rules below 0.55,
decision logging over the /events WebSocket, and the AI panel with Veto / Pick another / Mix now.

**Phase 5**
Implement quick_cut, filter_fade and echo_out transitions and the vocal-clash mix-point shift.

**Phase 6**
Add the optional DJ voice: Groq writes one line under 20 words, Kokoro speaks it, frontend ducks
music by 8 dB while it plays. UI toggle.

**Phase 7**
Mix recording to WAV and set history export (JSON + text tracklist).

## Tips
- After each phase: run it, listen, then tell Claude Code exactly what sounded wrong
  ("the blend starts half a beat late", "bass clashes at bar 16").
- Keep a 20-track test folder with mixed BPMs and keys.
