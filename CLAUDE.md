# JevDJ - AI DJ with a Virtual-DJ style interface

## What this is
A local desktop-style web app that plays a continuous DJ mix from the user's own music folder.
Jev (TypeSafe System One model) makes every DJ decision: next track, when to mix, which
transition style. The browser does the real audio mixing with the Web Audio API.
Groq writes optional short DJ voice lines; Kokoro TTS speaks them.

Owner: Nqobile Sibiya (Johannesburg). Default taste: amapiano, afro house, deep house, hip hop.

## Stack (locked)
- Backend: Python 3.11, FastAPI, uvicorn, librosa, numpy, typesafe-sdk, groq, kokoro (optional)
- Frontend: Vite + TypeScript + React, Web Audio API, wavesurfer.js for waveforms
- Storage: SQLite (tracks table with analysis cache)
- Run: `backend: uvicorn app.main:app --reload`, `frontend: npm run dev`

## Rules
- All Jev calls go through `backend/app/brain/jev.py` only. Never call Jev from the frontend.
- Every Jev decision is logged (question, options, answer, confidence) and shown in the AI panel.
- If Jev confidence < 0.40, fall back to the rule-based picker in `brain/rules.py` and mark it.
- Audio mixing happens only in the frontend (`src/audio/`). Backend never streams mixed audio.
- API keys only in `.env` (see `.env.example`). Never commit `.env`.
- Git: commits credited to Nqobile only. No Co-Authored-By trailers, no "Generated with" footers.
- Use hyphens, not em dashes, in docs and UI copy.
- UI must look like a pro DJ tool (dark, dense, neon accents), not a rounded SaaS dashboard.
  No purple gradients, no Inter/DM Sans. Use "JetBrains Mono" for numbers, "Space Grotesk" for labels.

## Key docs
- docs/SPEC.md - features, architecture, Jev decision design, UI layout, phases
- docs/KICKOFF_PROMPT.md - the prompts to paste into Claude Code, phase by phase
