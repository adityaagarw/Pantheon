"use client";

import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/lib/api";
import { useOrg } from "@/store/org";
import { AgentAvatar } from "./agent";
import { Button, cx, ErrorNote, Field, Input, Modal, Select, Textarea, Toggle } from "./ui";

const STYLES = [
  { value: "discussion", label: "Discussion", hint: "Share information and perspectives" },
  { value: "decision", label: "Decision", hint: "Converge on one outcome; everyone states a position" },
  { value: "brainstorm", label: "Brainstorm", hint: "Generate many ideas, no criticism at first" },
  { value: "standup", label: "Standup", hint: "Done / next / blockers — one quick round" },
  { value: "review", label: "Review", hint: "Critique a deliverable concretely" },
];

export function MeetingDialog({ open, onClose, rooms }: { open: boolean; onClose: () => void; rooms: string[] }) {
  const orgId = useOrg((s) => s.orgId)!;
  const org = useOrg((s) => s.org);
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const defaults = { max_rounds: 3, default_rounds: 2, create_tasks: false, ...((org?.settings as unknown as { meetings?: Record<string, unknown> })?.meetings ?? {}) } as {
    max_rounds: number;
    default_rounds: number;
    create_tasks: boolean;
  };
  const [facilitator, setFacilitator] = useState("");
  const [people, setPeople] = useState<string[]>([]);
  const [agenda, setAgenda] = useState("");
  const [style, setStyle] = useState("discussion");
  const [rounds, setRounds] = useState(defaults.default_rounds);
  const [detail, setDetail] = useState("brief");
  const [createTasks, setCreateTasks] = useState(defaults.create_tasks);
  const [room, setRoom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await fetch(`/api/v1/orgs/${orgId}/meetings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ facilitatorId: facilitator, participants: people.filter((p) => p !== facilitator), agenda, style, rounds, detail, createTasks, room: room || null }),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      });
      onClose();
      setAgenda("");
      setPeople([]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  void api;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Call a meeting"
      footer={
        <Button variant="primary" onClick={submit} loading={busy} disabled={!facilitator || !agenda.trim() || people.filter((p) => p !== facilitator).length === 0}>
          Start meeting
        </Button>
      }
    >
      <div className="grid gap-4">
        <Field label="Agenda">
          <Textarea rows={3} value={agenda} onChange={(e) => setAgenda(e.target.value)} placeholder="What should be discussed or decided?" />
        </Field>
        <Field label="Facilitator" hint="Leads the meeting and writes the minutes">
          <Select value={facilitator} onChange={(e) => setFacilitator(e.target.value)}>
            <option value="">Choose…</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} — {a.role}
              </option>
            ))}
          </Select>
        </Field>
        <div>
          <div className="mb-1.5 text-xs font-medium text-ink-2">Participants</div>
          <div className="flex flex-wrap gap-2">
            {agents
              .filter((a) => a.id !== facilitator)
              .map((a) => (
                <button
                  key={a.id}
                  onClick={() => setPeople((p) => (p.includes(a.id) ? p.filter((x) => x !== a.id) : [...p, a.id]))}
                  className={cx("flex items-center gap-1.5 rounded-full border py-0.5 pl-0.5 pr-2.5 text-xs cursor-pointer", people.includes(a.id) ? "border-accent bg-accent/15" : "border-line-2")}
                >
                  <AgentAvatar agent={a} size={18} /> {a.name}
                </button>
              ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Style" hint={STYLES.find((s) => s.value === style)?.hint}>
            <Select value={style} onChange={(e) => setStyle(e.target.value)}>
              {STYLES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={`Rounds (max ${defaults.max_rounds})`}>
            <Input type="number" min={1} max={defaults.max_rounds} value={rounds} disabled={style === "standup"} onChange={(e) => setRounds(Number(e.target.value))} />
          </Field>
          <Field label="Contributions">
            <Select value={detail} onChange={(e) => setDetail(e.target.value)}>
              <option value="brief">Brief (1–3 sentences)</option>
              <option value="detailed">Detailed (a paragraph)</option>
            </Select>
          </Field>
          <Field label="Room">
            <Select value={room} onChange={(e) => setRoom(e.target.value)}>
              <option value="">Any free meeting room</option>
              {rooms.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Toggle checked={createTasks} onChange={setCreateTasks} label="Turn agreed action items into assigned tasks" />
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}
