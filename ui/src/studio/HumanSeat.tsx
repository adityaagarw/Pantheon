/**
 * Answering as a person in the organization (an agent of kind human; engine
 * services/humans.py): what's waiting for them, and a reply that goes back to whoever wrote
 * (or finishes the flow step they were given).
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Agent, Message } from "@/lib/types";
import { useOrg } from "@/store/org";
import { agentHue, hsl, initials } from "./look";

export function HumanSeat({ agent }: { agent: Agent }) {
  const agents = useOrg((s) => s.agents);
  const [waiting, setWaiting] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.humanWaiting(agent.id).then((r) => setWaiting(r.waiting), () => {});
  }, [agent.id]);
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const from = (m: Message) =>
    m.senderType === "user" ? "You" : m.kind === "flow_step" ? "A flow step" : (agents[m.senderId]?.name ?? "Pantheon");

  const send = () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    api
      .humanAnswer(agent.id, text.trim())
      .then(() => {
        setText("");
        load();
      }, (e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="flex max-h-[55%] shrink-0 flex-col border-b border-[var(--st-edge)]">
      <div className="flex items-center gap-2 px-4 pb-2 pt-3">
        <span className="grid size-6 place-items-center rounded-md text-[10.5px] font-semibold text-white" style={{ background: hsl(agentHue(agent), 65, 50) }}>
          {initials(agent.name)}
        </span>
        <div className="text-[13px]">
          You answer as <span className="font-medium">{agent.name}</span>
        </div>
        <span className="ml-auto text-[11.5px] text-[var(--st-ink-3)]">{waiting.length ? `${waiting.length} waiting` : "Nothing waiting"}</span>
      </div>
      {waiting.length > 0 && (
        <>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4">
            {waiting.map((m) => (
              <div key={m.id} className="rounded-lg border border-[var(--st-edge)] bg-black/20 px-3 py-2">
                <div className="text-[11px] text-[var(--st-ink-3)]">{from(m)}</div>
                <div className="mt-0.5 whitespace-pre-wrap text-[13px] leading-relaxed text-[var(--st-ink-2)]">{m.content}</div>
              </div>
            ))}
          </div>
          <div className="grid gap-2 px-4 py-3">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.ctrlKey || e.metaKey) && send()}
              rows={3}
              placeholder={`${agent.name}'s answer… (Ctrl Enter sends)`}
              className="w-full resize-y rounded-lg border border-[var(--st-edge)] bg-black/20 px-3 py-2 text-[13px] text-[var(--st-ink)] outline-none focus:border-[var(--st-edge-2)]"
            />
            <div className="flex items-center gap-2">
              {error && <span className="text-[12px] text-[var(--st-red)]">{error}</span>}
              <button
                onClick={send}
                disabled={busy || !text.trim()}
                className="ml-auto rounded-lg px-3 py-1.5 text-[12.5px] text-white disabled:opacity-40"
                style={{ background: "linear-gradient(140deg,#9d8cff,#6c5cff)" }}
              >
                {busy ? "Sending…" : `Answer as ${agent.name}`}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
