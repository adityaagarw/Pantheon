/**
 * Voice I/O through the backend's speech-server proxy (audio.cpp, or any
 * OpenAI-compatible audio API), with the browser's own speech as a fallback.
 * - Recording is converted to 16 kHz mono PCM WAV (the most widely accepted
 *   input for local ASR models) before upload.
 * - Speech playback is queued so replies never talk over each other.
 * - Without a speech server, replies are read by the browser's voices and
 *   (if the user opted in) the mic uses the browser's speech recognition.
 */

export interface VoiceStatus {
  /** A speech server is reachable (speech in and out, live talk). */
  server: boolean;
  /** Read aloud with the browser's voices when there's no server. */
  browserTts: boolean;
  /** Use the browser's speech recognition for the mic when there's no server. */
  browserStt: boolean;
  language: string;
}

let statusCache: { at: number; value: Promise<VoiceStatus> } | null = null;

/** What voice is available right now (cached for 30 s). */
export function voiceStatus(fresh = false): Promise<VoiceStatus> {
  if (!fresh && statusCache && Date.now() - statusCache.at < 30000) return statusCache.value;
  const value = fetch("/api/v1/voice/status")
    .then((r) => (r.ok ? (r.json() as Promise<VoiceStatus>) : Promise.reject(new Error(String(r.status)))))
    .catch(() => ({ server: false, browserTts: true, browserStt: false, language: "" }));
  statusCache = { at: Date.now(), value };
  return value;
}

/** Browser voices are stored on agents as "browser:<voice name>". */
export const BROWSER_VOICE = "browser:";

export function browserTtsSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** The browser's voices (they load asynchronously in some browsers). */
export function browserVoices(): Promise<SpeechSynthesisVoice[]> {
  if (!browserTtsSupported()) return Promise.resolve([]);
  const now = speechSynthesis.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => resolve(speechSynthesis.getVoices());
    speechSynthesis.addEventListener("voiceschanged", done, { once: true });
    setTimeout(done, 1500);
  });
}

/** The agent's chosen browser voice, or a stable pick per agent so voices differ. */
function pickVoice(all: SpeechSynthesisVoice[], wanted: string, agentId?: string): SpeechSynthesisVoice | undefined {
  if (wanted.startsWith(BROWSER_VOICE)) {
    const named = all.find((v) => v.name === wanted.slice(BROWSER_VOICE.length));
    if (named) return named;
  }
  const lang = (navigator.language || "en").slice(0, 2).toLowerCase();
  const same = all.filter((v) => v.lang.toLowerCase().startsWith(lang));
  const pool = same.length ? same : all;
  if (!pool.length) return undefined;
  if (!agentId) return pool.find((v) => v.default) ?? pool[0];
  let h = 0;
  for (const c of agentId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return pool[h % pool.length];
}

/** Short pieces: some browsers stop long utterances part-way. */
function sentences(text: string): string[] {
  const out: string[] = [];
  for (const s of text.match(/[^.!?\n]+[.!?]*\s*/g) ?? [text]) {
    const last = out[out.length - 1];
    if (last !== undefined && last.length + s.length < 220) out[out.length - 1] = last + s;
    else out.push(s);
  }
  return out.map((s) => s.trim()).filter(Boolean);
}

export async function transcribe(blob: Blob): Promise<string> {
  const wav = await toWav16k(blob);
  const form = new FormData();
  form.append("file", wav, "speech.wav");
  const res = await fetch("/api/v1/voice/transcribe", { method: "POST", body: form });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return ((await res.json()).text as string) ?? "";
}

async function toWav16k(blob: Blob): Promise<Blob> {
  const buf = await blob.arrayBuffer();
  const ctx = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await ctx.decodeAudioData(buf);
  } finally {
    void ctx.close();
  }
  const rate = 16000;
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  const pcm = rendered.getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o: number, s: string) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF");
  out.setUint32(4, 36 + pcm.length * 2, true);
  w(8, "WAVE");
  w(12, "fmt ");
  out.setUint32(16, 16, true);
  out.setUint16(20, 1, true);
  out.setUint16(22, 1, true);
  out.setUint32(24, rate, true);
  out.setUint32(28, rate * 2, true);
  out.setUint16(32, 2, true);
  out.setUint16(34, 16, true);
  w(36, "data");
  out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const v = Math.max(-1, Math.min(1, pcm[i]));
    out.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([out.buffer], { type: "audio/wav" });
}

export class Recorder {
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private stream: MediaStream | null = null;

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    this.chunks = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.rec.start();
  }

  stop(): Promise<Blob> {
    return new Promise((resolve) => {
      const rec = this.rec;
      if (!rec) return resolve(new Blob());
      rec.onstop = () => {
        this.stream?.getTracks().forEach((t) => t.stop());
        resolve(new Blob(this.chunks, { type: rec.mimeType || "audio/webm" }));
      };
      rec.stop();
    });
  }
}

const queue: { text: string; agentId?: string; voice?: string; done?: (spoken: boolean) => void }[] = [];
let playing = false;
let current: HTMLAudioElement | null = null;
const speakingListeners = new Set<(speaking: boolean) => void>();

/** Subscribe to "an agent is speaking out loud" changes. Returns an unsubscribe. */
export function onSpeakingChange(fn: (speaking: boolean) => void): () => void {
  speakingListeners.add(fn);
  return () => speakingListeners.delete(fn);
}

function setPlaying(v: boolean): void {
  if (playing === v) return;
  playing = v;
  speakingListeners.forEach((fn) => fn(v));
}

export function isSpeaking(): boolean {
  return playing;
}

let finishCurrent: (() => void) | null = null;
let currentDone: ((spoken: boolean) => void) | null = null;
let generation = 0;

export function speak(text: string, agentId?: string, voice?: string): void {
  void speakAndWait(text, agentId, voice);
}

/**
 * Speak and resolve when this text has finished playing: true if it was
 * spoken, false if it was cut off (stopSpeaking) or couldn't be synthesized.
 */
export function speakAndWait(text: string, agentId?: string, voice?: string): Promise<boolean> {
  const clean = text
    .replace(/```[\s\S]*?```/g, " (code omitted) ")
    .replace(/\$([^$]+)\$/g, "$1")
    .replace(/[*_#>`\\]/g, "")
    .trim();
  if (!clean) return Promise.resolve(true);
  return new Promise((done) => {
    queue.push({ text: clean.slice(0, 1500), agentId, voice, done });
    if (!playing) void drain();
  });
}

/** Stop talking now (barge-in) and drop anything queued. */
export function stopSpeaking(): void {
  generation += 1;
  for (const item of queue.splice(0)) item.done?.(false);
  currentDone?.(false);
  currentDone = null;
  current?.pause();
  current = null;
  if (browserTtsSupported()) speechSynthesis.cancel();
  finishCurrent?.();
  finishCurrent = null;
  setPlaying(false);
}

async function drain(): Promise<void> {
  const gen = generation;
  setPlaying(true);
  while (queue.length && gen === generation) {
    const item = queue.shift()!;
    currentDone = item.done ?? null;
    let spoken = false;
    try {
      const res = await fetch("/api/v1/voice/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text, agentId: item.agentId, voice: item.voice }),
      });
      if (res.ok && gen === generation) {
        const url = URL.createObjectURL(await res.blob());
        if (gen === generation) {
          spoken = await new Promise<boolean>((resolve) => {
            const a = new Audio(url);
            current = a;
            const finish = (ok: boolean) => {
              URL.revokeObjectURL(url);
              resolve(ok);
            };
            finishCurrent = () => finish(false);
            a.onended = () => finish(true);
            a.onerror = () => finish(false);
            a.play().catch(() => finish(false));
          });
        } else {
          URL.revokeObjectURL(url);
        }
      } else if (gen === generation) {
        // No speech server (or a browser voice was chosen): the browser reads it.
        const hint = (await res.json().catch(() => null)) as { browser?: boolean; voice?: string } | null;
        const voice = hint?.voice ?? "";
        if (hint?.browser && browserTtsSupported() && (voice.startsWith(BROWSER_VOICE) || (await voiceStatus()).browserTts)) {
          spoken = await browserSay(item.text, voice, item.agentId, gen);
        }
      }
    } catch {
      /* speech is best-effort */
    }
    // stopSpeaking() already told this item's waiter it was cut off.
    if (gen === generation) {
      item.done?.(spoken);
      currentDone = null;
    }
  }
  if (gen === generation) {
    current = null;
    finishCurrent = null;
    setPlaying(false);
  }
}

async function browserSay(text: string, wanted: string, agentId: string | undefined, gen: number): Promise<boolean> {
  const voice = pickVoice(await browserVoices(), wanted, agentId);
  for (const part of sentences(text)) {
    if (gen !== generation) return false;
    const ok = await new Promise<boolean>((resolve) => {
      const u = new SpeechSynthesisUtterance(part);
      if (voice) {
        u.voice = voice;
        u.lang = voice.lang;
      }
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      finishCurrent = () => resolve(false);
      speechSynthesis.speak(u);
    });
    if (!ok) return false;
  }
  return true;
}

/* ---------- listening: speech server, or the browser's recognizer ---------- */

/** A running capture; stop() returns what was said. */
export interface Listener {
  stop(): Promise<string>;
}

/** Voice input isn't available; the message says how to set it up. */
export class VoiceSetupError extends Error {}

interface RecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}
interface Recognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<RecognitionResult> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function browserSttSupported(): boolean {
  return recognitionCtor() !== null;
}

/** Can the mic be used at all (a server, or opted-in browser recognition)? */
export function canListen(status: VoiceStatus | null): boolean {
  return !!status && (status.server || (status.browserStt && browserSttSupported()));
}

/** Start capturing speech with the best available engine. */
export async function listen(): Promise<Listener> {
  const status = await voiceStatus();
  if (status.server) {
    const rec = new Recorder();
    await rec.start();
    return { stop: async () => transcribe(await rec.stop()) };
  }
  const Ctor = recognitionCtor();
  if (status.browserStt && Ctor) return browserListen(Ctor, status.language);
  throw new VoiceSetupError(
    status.browserStt
      ? "This browser has no speech recognition. Set up a speech server in Settings → Voice."
      : "Voice input needs a speech server, or browser speech recognition turned on, in Settings → Voice.",
  );
}

function browserListen(Ctor: RecognitionCtor, language: string): Listener {
  const r = new Ctor();
  r.continuous = true;
  r.interimResults = false;
  r.lang = language || navigator.language;
  const heard: string[] = [];
  let error = "";
  let ended = false;
  let onEnded: (() => void) | null = null;
  r.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) heard.push(e.results[i][0].transcript);
  };
  r.onerror = (e) => {
    error = e.error;
  };
  r.onend = () => {
    ended = true;
    onEnded?.();
  };
  r.start();
  return {
    stop: () =>
      new Promise<string>((resolve, reject) => {
        const finish = () => {
          const text = heard.join(" ").trim();
          if (!text && error && error !== "no-speech" && error !== "aborted") reject(new Error(`browser speech recognition: ${error}`));
          else resolve(text);
        };
        if (ended) return finish();
        onEnded = finish;
        r.stop();
      }),
  };
}
