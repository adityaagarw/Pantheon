/**
 * Hands-free conversation with an agent.
 *
 * The microphone is captured with echo cancellation, downsampled to 16 kHz
 * mono PCM16 in an AudioWorklet and streamed to the backend, which runs
 * audio.cpp's Silero VAD to find where you start and stop talking, transcribes
 * each utterance and sends it to the agent. Agent replies are spoken by the
 * page; talking over the agent stops it (unless half-duplex is on, in which
 * case the mic is muted while the agent talks — safest with speakers).
 */

import { isSpeaking, onSpeakingChange, stopSpeaking } from "./voice";
import { backendWsBase } from "./ws";

export type LiveState = "connecting" | "listening" | "hearing" | "transcribing" | "agent-speaking" | "paused" | "error" | "off";

export interface LiveCallbacks {
  onState: (s: LiveState) => void;
  onTranscript: (text: string) => void;
  onLevel: (level: number) => void; // 0..1
  onInfo: (info: { vad: string; fallbackReason?: string | null }) => void;
  onError: (message: string) => void;
}

const WORKLET = `
class Pcm16Downsampler extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.acc = []; this.pos = 0; this.level = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let peak = 0;
    for (let i = 0; i < ch.length; i++) { const v = Math.abs(ch[i]); if (v > peak) peak = v; }
    this.level = Math.max(peak, this.level * 0.85);
    // Linear-interpolated resampling to 16 kHz.
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const a = ch[i], b = i + 1 < ch.length ? ch[i + 1] : a;
      this.acc.push(a + (b - a) * f);
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    if (this.acc.length >= 1600) { // 100 ms
      const out = new Int16Array(this.acc.length);
      for (let i = 0; i < this.acc.length; i++) { const s = Math.max(-1, Math.min(1, this.acc[i])); out[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
      this.port.postMessage({ pcm: out.buffer, level: this.level }, [out.buffer]);
      this.acc = [];
    }
    return true;
  }
}
registerProcessor("pcm16-downsampler", Pcm16Downsampler);
`;

export class LiveTalk {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private unsubSpeaking: (() => void) | null = null;
  private state: LiveState = "off";
  private muted = false;
  private awaitingReply = false;

  constructor(
    private orgId: string,
    private agentId: string,
    private cb: LiveCallbacks,
    private opts: { halfDuplex: boolean; silenceMs?: number } = { halfDuplex: true },
  ) {}

  private set(s: LiveState) {
    this.state = s;
    this.cb.onState(s);
  }

  async start(): Promise<void> {
    this.set("connecting");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (e) {
      const secure = window.isSecureContext;
      this.fail(secure ? `Microphone unavailable: ${(e as Error).message}` : "The microphone needs HTTPS (or localhost). Open Pantheon via the HTTPS address to talk from this device.");
      return;
    }
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm16-downsampler");
    src.connect(this.node);

    const q = new URLSearchParams({ org: this.orgId, agent: this.agentId, silence: String(this.opts.silenceMs ?? 700) });
    const ws = new WebSocket(`${backendWsBase()}/api/v1/voice/live?${q}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (m) => this.onServer(JSON.parse(m.data as string));
    ws.onerror = () => this.fail("Live voice connection failed.");
    ws.onclose = () => {
      if (this.state !== "off" && this.state !== "error") this.fail("Live voice connection closed.");
    };
    this.node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      this.cb.onLevel(Math.min(1, e.data.level * 2.5));
      const agentTalking = isSpeaking();
      if (this.muted || (this.opts.halfDuplex && agentTalking)) return;
      if (ws.readyState === WebSocket.OPEN) ws.send(e.data.pcm);
    };
    this.unsubSpeaking = onSpeakingChange((talking) => {
      if (this.state === "off" || this.state === "error") return;
      if (talking) {
        this.awaitingReply = false;
        this.set("agent-speaking");
      } else if (this.state === "agent-speaking") this.set(this.muted ? "paused" : "listening");
    });
  }

  private onServer(e: { type: string; text?: string; vad?: string; fallbackReason?: string | null; message?: string; ignored?: boolean }) {
    switch (e.type) {
      case "ready":
        this.cb.onInfo({ vad: e.vad ?? "", fallbackReason: e.fallbackReason });
        this.set("listening");
        break;
      case "speech_start":
        if (isSpeaking()) stopSpeaking(); // barge-in
        this.set("hearing");
        break;
      case "speech_end":
        this.set("transcribing");
        break;
      case "transcript":
        if (e.ignored || !e.text) this.set("listening");
        else this.cb.onTranscript(e.text);
        break;
      case "sent":
        this.awaitingReply = true;
        this.set("listening");
        break;
      case "vad":
        this.cb.onInfo({ vad: e.vad ?? "", fallbackReason: e.fallbackReason });
        break;
      case "error":
        this.cb.onError(e.message ?? "voice error");
        if (this.state === "transcribing") this.set("listening");
        break;
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.state === "listening" || this.state === "paused") this.set(muted ? "paused" : "listening");
  }

  get waitingForReply(): boolean {
    return this.awaitingReply;
  }

  private fail(message: string) {
    this.cb.onError(message);
    this.teardown();
    this.set("error");
  }

  stop(): void {
    try {
      this.ws?.send(JSON.stringify({ type: "stop" }));
    } catch {
      /* closing anyway */
    }
    this.teardown();
    this.set("off");
  }

  private teardown() {
    this.unsubSpeaking?.();
    this.unsubSpeaking = null;
    this.node?.disconnect();
    this.node = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.ctx?.close();
    this.ctx = null;
    const ws = this.ws;
    this.ws = null;
    if (ws && ws.readyState <= WebSocket.OPEN) setTimeout(() => ws.close(), 300);
  }
}
