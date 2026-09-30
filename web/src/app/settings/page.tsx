"use client";

import { useCallback, useEffect, useState } from "react";
import { McpServersPanel } from "@/components/settings/McpServersPanel";
import { Badge, Button, Card, ErrorNote, Field, Input, Modal, Select, Tabs, Toggle } from "@/components/ui";
import { api, type VoiceConfig } from "@/lib/api";
import type { Provider, ProviderModel } from "@/lib/types";
import { browserSttSupported, browserTtsSupported, listen, speak, voiceStatus, type Listener } from "@/lib/voice";

export default function SettingsPage() {
  const [tab, setTab] = useState<"providers" | "mcp" | "voice">("providers");
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6 md:px-6 md:py-8">
        <h1 className="text-xl font-semibold">Settings</h1>
        <Tabs
          className="no-scrollbar mt-4 w-fit max-w-full overflow-x-auto"
          value={tab}
          onChange={setTab}
          items={[
            { value: "providers", label: "LLM providers" },
            { value: "mcp", label: "MCP servers (global)" },
            { value: "voice", label: "Voice" },
          ]}
        />
        <div className="mt-6">
          {tab === "providers" && <Providers />}
          {tab === "mcp" && (
            <Card className="p-5">
              <div className="mb-4 text-sm text-ink-2">Global MCP servers are available to every organization. Grant tools to individual agents on each org&apos;s Team tab.</div>
              <McpServersPanel />
            </Card>
          )}
          {tab === "voice" && <Voice />}
        </div>
      </div>
    </div>
  );
}

const PRESETS: { label: string; type: Provider["type"]; name: string; baseUrl?: string; model?: string }[] = [
  { label: "OpenAI", type: "openai", name: "OpenAI", model: "gpt-5.1" },
  { label: "OpenRouter", type: "openrouter", name: "OpenRouter" },
  { label: "Local vLLM / llama.cpp / LM Studio", type: "openai_compatible", name: "Local model", baseUrl: "http://host.docker.internal:8000/v1" },
  { label: "Ollama", type: "ollama", name: "Ollama", baseUrl: "http://host.docker.internal:11434/v1" },
  { label: "Anthropic", type: "anthropic", name: "Anthropic" },
];

function Providers() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [editing, setEditing] = useState<Provider | "new" | null>(null);
  const [tests, setTests] = useState<Record<string, { busy?: boolean; text?: string; ok?: boolean }>>({});
  const load = useCallback(() => api.providers().then(setProviders).catch(() => {}), []);
  useEffect(() => {
    void load();
  }, [load]);

  const test = async (p: Provider) => {
    setTests((t) => ({ ...t, [p.id]: { busy: true } }));
    const r = await api.testProvider(p.id).catch((e) => ({ ok: false, error: e.message }) as Awaited<ReturnType<typeof api.testProvider>>);
    setTests((t) => ({
      ...t,
      [p.id]: { ok: r.ok, text: r.ok ? `✓ ${r.model} replied "${r.reply}"${r.models ? ` · ${r.models.length} models available` : ""}` : `✗ ${r.error ?? r.modelsError}` },
    }));
  };

  return (
    <div className="space-y-3">
      {providers.map((p) => (
        <Card key={p.id} className="p-4">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{p.name}</span>
            <Badge>{p.type}</Badge>
            {p.isDefault && <Badge tone="accent">default</Badge>}
            <div className="ml-auto flex gap-1.5">
              <Button size="sm" onClick={() => test(p)} loading={tests[p.id]?.busy}>
                Test
              </Button>
              {!p.isDefault && (
                <Button size="sm" variant="ghost" onClick={() => api.updateProvider(p.id, { isDefault: true }).then(load)}>
                  Make default
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>
                Edit
              </Button>
            </div>
          </div>
          <div className="mt-1.5 text-xs text-ink-3">
            {p.baseUrl ?? "default endpoint"} · {p.hasApiKey ? "API key set" : "no API key"} · models: {p.models.map((m) => m.id).join(", ") || "—"}
            {p.defaultModel && ` · default ${p.defaultModel}`}
          </div>
          {tests[p.id]?.text && <div className={`mt-2 text-xs ${tests[p.id]?.ok ? "text-ok" : "text-bad"}`}>{tests[p.id]?.text}</div>}
        </Card>
      ))}
      <Button variant="primary" onClick={() => setEditing("new")}>
        + Add provider
      </Button>
      {editing && (
        <ProviderEditor
          provider={editing === "new" ? null : editing}
          onClose={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

function ProviderEditor({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const [form, setForm] = useState({
    name: provider?.name ?? "",
    type: provider?.type ?? ("openai_compatible" as Provider["type"]),
    baseUrl: provider?.baseUrl ?? "",
    apiKey: "",
    defaultModel: provider?.defaultModel ?? "",
    models: provider?.models ?? ([] as ProviderModel[]),
  });
  const [remote, setRemote] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setError(null);
    const models = form.models.length ? form.models : form.defaultModel ? [{ id: form.defaultModel }] : [];
    const body: Record<string, unknown> = { name: form.name || form.type, baseUrl: form.baseUrl || null, defaultModel: form.defaultModel || null, models };
    if (form.apiKey) body.apiKey = form.apiKey;
    try {
      if (provider) await api.updateProvider(provider.id, body);
      else await api.createProvider({ ...body, type: form.type });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fetchModels = async () => {
    if (!provider) return;
    const r = await api.testProvider(provider.id);
    setRemote(r.models ?? []);
    if (r.modelsError) setError(r.modelsError);
  };

  const setModel = (i: number, patch: Partial<ProviderModel>) => setForm({ ...form, models: form.models.map((m, k) => (k === i ? { ...m, ...patch } : m)) });

  return (
    <Modal
      open
      wide
      onClose={onClose}
      title={provider ? `Edit ${provider.name}` : "Add LLM provider"}
      footer={
        <>
          {provider && (
            <Button variant="danger" onClick={() => api.deleteProvider(provider.id).then(onClose).catch((e) => setError(e.message))}>
              Delete
            </Button>
          )}
          <Button variant="primary" onClick={save} loading={busy}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {!provider && (
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                className="rounded-full border border-line-2 px-2.5 py-1 text-xs text-ink-2 hover:text-ink cursor-pointer"
                onClick={() => setForm({ ...form, type: p.type, name: p.name, baseUrl: p.baseUrl ?? "", defaultModel: p.model ?? "", models: p.model ? [{ id: p.model }] : [] })}
              >
                {p.label}
              </button>
            ))}
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Type">
            <Select disabled={!!provider} value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as Provider["type"] })}>
              <option value="openai">OpenAI</option>
              <option value="openrouter">OpenRouter</option>
              <option value="openai_compatible">OpenAI-compatible (vLLM, llama.cpp, LM Studio…)</option>
              <option value="ollama">Ollama</option>
              <option value="anthropic">Anthropic</option>
              <option value="mock">Mock (offline)</option>
            </Select>
          </Field>
          <Field label="Base URL" hint={form.type === "openai_compatible" ? "required" : "optional"}>
            <Input value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} placeholder="http://host:port/v1" />
          </Field>
          <Field label="API key" hint={provider?.hasApiKey ? "set — leave empty to keep" : "stored encrypted"}>
            <Input type="password" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} />
          </Field>
          <Field label="Default model">
            <Input list="remote-models" value={form.defaultModel} onChange={(e) => setForm({ ...form, defaultModel: e.target.value })} />
            <datalist id="remote-models">
              {remote.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          {provider && (
            <div className="flex items-end">
              <Button onClick={fetchModels}>Fetch available models</Button>
            </div>
          )}
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-ink-2">Models (context window & pricing drive compaction and cost tracking)</span>
            <Button size="sm" variant="ghost" onClick={() => setForm({ ...form, models: [...form.models, { id: "" }] })}>
              + Add
            </Button>
          </div>
          <div className="space-y-2">
            {form.models.map((m, i) => (
              <div key={i} className="grid grid-cols-2 items-center gap-2 sm:grid-cols-[2fr_1fr_1fr_1fr_auto_auto]">
                <Input list="remote-models" placeholder="model id" value={m.id} onChange={(e) => setModel(i, { id: e.target.value })} />
                <Input type="number" placeholder="context" value={m.context_window ?? ""} onChange={(e) => setModel(i, { context_window: e.target.value ? Number(e.target.value) : undefined })} />
                <Input type="number" step="0.01" placeholder="$ in / 1M" value={m.input_per_mtok ?? ""} onChange={(e) => setModel(i, { input_per_mtok: e.target.value ? Number(e.target.value) : undefined })} />
                <Input type="number" step="0.01" placeholder="$ out / 1M" value={m.output_per_mtok ?? ""} onChange={(e) => setModel(i, { output_per_mtok: e.target.value ? Number(e.target.value) : undefined })} />
                <label className="flex items-center gap-1.5 text-xs text-ink-2" title="The model accepts images (screenshots)">
                  <input type="checkbox" checked={!!m.vision} onChange={(e) => setModel(i, { vision: e.target.checked || undefined })} /> vision
                </label>
                <Button variant="ghost" onClick={() => setForm({ ...form, models: form.models.filter((_, k) => k !== i) })}>
                  ✕
                </Button>
              </div>
            ))}
          </div>
        </div>
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}

/** One-click starting points; any OpenAI-compatible audio API works. */
const VOICE_PRESETS: { label: string; hint: string; cfg: Partial<VoiceConfig> }[] = [
  {
    label: "audio.cpp (local)",
    hint: "Private and free: runs on your machine",
    cfg: { baseUrl: "http://host.docker.internal:8080", sttModel: "qwen3-asr", ttsModel: "pocket-tts", defaultVoice: "" },
  },
  {
    label: "OpenAI",
    hint: "Needs an API key",
    cfg: { baseUrl: "https://api.openai.com", sttModel: "gpt-4o-mini-transcribe", ttsModel: "gpt-4o-mini-tts", defaultVoice: "alloy" },
  },
  {
    label: "Groq (speech-to-text)",
    hint: "Fast hosted Whisper; replies are read by the browser",
    cfg: { baseUrl: "https://api.groq.com/openai", sttModel: "whisper-large-v3-turbo", ttsModel: "", defaultVoice: "" },
  },
];

function Voice() {
  const [cfg, setCfg] = useState<VoiceConfig | null>(null);
  const [health, setHealth] = useState<Awaited<ReturnType<typeof api.voiceHealth>> | null>(null);
  const [saved, setSaved] = useState(false);
  const [heard, setHeard] = useState<string | null>(null);
  const [rec, setRec] = useState<Listener | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState("");
  useEffect(() => {
    api.voiceConfig().then(setCfg).catch(() => {});
    api.voiceHealth().then(setHealth).catch(() => {});
  }, []);
  if (!cfg) return null;
  const save = async (patch: Partial<VoiceConfig> = {}) => {
    const body: Partial<VoiceConfig> = { ...cfg, ...patch };
    delete body.hasApiKey;
    if (key) body.apiKey = key;
    const c = await api.saveVoiceConfig(body);
    setCfg(c);
    setKey("");
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    void voiceStatus(true);
    setHealth(null);
    api.voiceHealth().then(setHealth).catch(() => {});
  };
  return (
    <Card className="p-5">
      <div className="mb-1 text-sm font-semibold">Voice</div>
      <p className="mb-4 text-sm text-ink-2">
        Optional. Without a speech server, agents read their replies with this browser&apos;s built-in voices. Add a speech server for better voices, the
        microphone and hands-free <b>Live</b> talk: a local <code>audiocpp_server</code>, or any OpenAI-compatible audio API.
      </p>
      <div className="mb-4 flex flex-wrap gap-2">
        {VOICE_PRESETS.map((p) => (
          <Button key={p.label} size="sm" title={p.hint} onClick={() => setCfg({ ...cfg, ...p.cfg })}>
            {p.label}
          </Button>
        ))}
      </div>
      <div className="mb-4 flex items-center gap-2 text-sm">
        Speech server:
        {health ? (
          health.ok ? (
            <Badge tone="ok">connected{health.models?.length ? ` · ${health.models.slice(0, 6).join(", ")}${health.models.length > 6 ? "…" : ""}` : ""}</Badge>
          ) : (
            <Badge tone={cfg.baseUrl ? "bad" : undefined}>{cfg.baseUrl ? (health.error ?? "unreachable") : "none (browser voices only)"}</Badge>
          )
        ) : (
          <Badge>checking…</Badge>
        )}
      </div>
      {health?.ok && health.vad && (
        <div className="-mt-2 mb-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            Live talk voice detection:
            <Badge tone={health.vadError ? "warn" : "ok"}>{health.vad}</Badge>
          </div>
          {health.vadError && (
            <div className="mt-1 text-xs text-ink-3">
              audio.cpp VAD not used: {health.vadError}. Live talk still works with the built-in energy detector. With audio.cpp, the backend&apos;s voice
              folder ({health.shareDir}) must be visible to it at {health.shareHostDir ?? "the same path"}: set PANTHEON_VOICE_SHARE_HOST_DIR.
            </div>
          )}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Server URL" hint="leave empty for no server">
          <Input value={cfg.baseUrl} placeholder="http://host.docker.internal:8080" onChange={(e) => setCfg({ ...cfg, baseUrl: e.target.value })} />
        </Field>
        <Field label="API key" hint={cfg.hasApiKey ? "saved; type to replace" : "for hosted APIs; not needed for audio.cpp"}>
          <div className="flex gap-2">
            <Input type="password" autoComplete="off" value={key} placeholder={cfg.hasApiKey ? "••••••••" : ""} onChange={(e) => setKey(e.target.value)} />
            {cfg.hasApiKey && (
              <Button size="sm" onClick={() => void save({ apiKey: "" })}>
                Clear
              </Button>
            )}
          </div>
        </Field>
        <Field label="Speech-to-text model">
          <Input list="voice-models" value={cfg.sttModel} onChange={(e) => setCfg({ ...cfg, sttModel: e.target.value })} />
        </Field>
        <Field label="Text-to-speech model" hint="empty: the server's default">
          <Input list="voice-models" value={cfg.ttsModel} onChange={(e) => setCfg({ ...cfg, ttsModel: e.target.value })} />
        </Field>
        <datalist id="voice-models">
          {(health?.models ?? []).map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <Field label="Default voice">
          <Input value={cfg.defaultVoice} onChange={(e) => setCfg({ ...cfg, defaultVoice: e.target.value })} />
        </Field>
        <Field label="Language" hint="optional, e.g. en">
          <Input value={cfg.language} onChange={(e) => setCfg({ ...cfg, language: e.target.value })} />
        </Field>
      </div>
      <div className="mt-5 grid gap-3">
        <Toggle checked={cfg.autoSpeak} onChange={(v) => setCfg({ ...cfg, autoSpeak: v })} label="Speak agent replies by default" />
        <Toggle
          checked={cfg.browserTts}
          onChange={(v) => setCfg({ ...cfg, browserTts: v })}
          label={`Use this browser's voices when there's no speech server${browserTtsSupported() ? "" : " (not supported here)"}`}
        />
        <div>
          <Toggle
            checked={cfg.browserStt}
            onChange={(v) => setCfg({ ...cfg, browserStt: v })}
            label={`Use the browser's speech recognition for the mic when there's no speech server${browserSttSupported() ? "" : " (not supported in this browser)"}`}
          />
          <div className="mt-1 text-xs text-ink-3">
            Off by default: Chrome and Edge send your audio to Google, and Safari to Apple, to transcribe it. Firefox doesn&apos;t support it.
          </div>
        </div>
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => void save()}>
          Save
        </Button>
        {saved && <span className="text-sm text-ok">Saved</span>}
        <Button onClick={() => speak("Hello! This is how Pantheon's agents will sound.")}>▶ Test speech</Button>
        <Button
          variant={rec ? "danger" : "secondary"}
          onClick={async () => {
            setError(null);
            if (!rec) {
              try {
                setRec(await listen());
              } catch (e) {
                setError((e as Error).message);
              }
            } else {
              setRec(null);
              try {
                setHeard(await rec.stop());
              } catch (e) {
                setError((e as Error).message);
              }
            }
          }}
        >
          {rec ? "■ Stop & transcribe" : "● Test microphone"}
        </Button>
      </div>
      {heard !== null && <div className="mt-3 rounded-md border border-line bg-bg p-3 text-sm">Heard: “{heard}”</div>}
      <div className="mt-3">
        <ErrorNote error={error} />
      </div>
    </Card>
  );
}
