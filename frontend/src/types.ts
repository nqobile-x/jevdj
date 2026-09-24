export type Vibe = "auto" | "warm-up" | "build" | "peak" | "cool-down";
export type Phase = Exclude<Vibe, "auto">;
export type TransitionStyle =
  | "long_blend" | "quick_cut" | "filter_fade" | "echo_out" | "wash_out" | "brake" | "loop_roll"
  | "chop" | "tease" | "rewind";
export type MixStyle = "radio" | "club" | "quick";
export type Flair = "smooth" | "creative" | "turnt";
export type DeckId = "A" | "B";

export interface TrackSummary {
  id: number;
  path: string;
  title: string;
  artist: string;
  genre: string;
  duration: number;
  bpm: number;
  beat_period: number;
  first_beat: number;
  first_downbeat: number;
  key_name: string;
  camelot: string;
  key_confidence: number;
  energy: number;
  intro_end: number;
  outro_start: number;
  source?: "audius" | null;
  permalink?: string | null;
}

export interface TrackDetail extends TrackSummary {
  energy_bars: number[];
  vocal_bars: number[];
  downbeats: number[];
}

export interface NextResponse {
  track: TrackSummary;
  source: "jev" | "rules" | "order";
  confidence: number | null;
  reason: string;
  fallback: string | null;
  decision_ids: number[];
  phase: Phase;
  candidates: number;
}

export interface TransitionPlan {
  style: TransitionStyle;
  bars: number;
  mix_out_s: number;
  mix_in_s: number;
  rate: number;
  out_bar: number;
  shifted_for_vocals: boolean;
  notes: string[];
  source: "jev" | "rules";
  confidence: number | null;
  fallback: string | null;
  tempo_ramp?: { out_rate: number; bars: number } | null;
  loop?: { start_s: number; end_s: number; bars: number } | null;
  pre?: { type: "run_it_back"; bar: number; jump_to_s: number; bars: number } | null;
  edit?: { at_s: number; to_s: number; from_bar: number; to_bar: number } | null;
}

export interface LearnedStyle {
  up: number;
  down: number;
  n: number;
  bonus: number;
}

export interface Decision {
  id: number;
  ts: number;
  kind: string;
  source: "jev" | "rules" | "user" | "groq" | "template" | "order";
  question: string | null;
  options: unknown;
  answer: unknown;
  confidence: number | null;
  probabilities: Record<string, number> | null;
  reason: string | null;
}

export interface Health {
  ok: boolean;
  jev_online: boolean;
  voice: { groq: boolean; kokoro: boolean };
  music_dir: string;
  music_dir_exists: boolean;
  tracks: number;
  set_id: number | null;
  phase: Phase;
  vibe: Vibe;
}

export type ServerEvent =
  | { type: "hello"; jev_online: boolean; phase: Phase; vibe: Vibe }
  | { type: "decision"; decision: Decision }
  | { type: "scan_started"; total: number; cached: number; root: string }
  | { type: "scan_progress"; done: number; total: number; current: string; error: string | null }
  | { type: "scan_finished"; analysed?: number; failed?: number; removed?: number; total_files?: number; error?: string; quiet?: boolean }
  | { type: "phase"; phase: Phase; vibe: Vibe; source: string }
  | { type: "set_started"; set_id: number; vibe: Vibe; phase: Phase }
  | { type: "played"; track_id: number; played: number }
  | { type: "library_change"; added: string[]; removed: number; changed: number }
  | { type: "scan_reason"; reason: string }
  | { type: "mix_style"; mix_style: MixStyle }
  | { type: "source_progress"; source: string; id: string; stage: string; title?: string; track_id?: number; error?: string }
  | ({ type: "radio" } & RadioStatus)
  | ({ type: "order" } & OrderStatus);

export type OrderShape = "journey" | "build" | "peak";

export interface OrderItem {
  id: number;
  pos: number;
  energy: number;
  played: boolean;
  smooth: 0 | 1 | 2 | 3 | null; // how smooth the mix INTO this track is
}

export interface OrderStatus {
  active: boolean;
  shape: OrderShape;
  scope: "library" | "audius";
  total: number;
  smooth_pct: number | null;
  items: OrderItem[];
}

export interface RadioStatus {
  active: boolean;
  query: string | null;
  genre: string | null;
  ready: number;
  total: number;
  adding: number;
}

export interface VoiceLine {
  text: string;
  source: string;
  audio_url: string | null;
}
