"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Org } from "@/lib/types";
import { notifyOrgsChanged } from "./AppShell";
import { Button, ErrorNote, Input, Modal } from "./ui";

/** Confirm-by-typing-the-name dialog for deleting an organization. */
export function DeleteOrgDialog({ org, open, onClose, onDeleted }: { org: Pick<Org, "id" | "name" | "agentCount" | "openTasks">; open: boolean; onClose: () => void; onDeleted?: () => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const close = () => {
    setTyped("");
    setError(null);
    onClose();
  };
  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteOrg(org.id);
      notifyOrgsChanged();
      close();
      onDeleted?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={close}
      title={`Delete ${org.name}?`}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button variant="danger" loading={busy} disabled={typed.trim() !== org.name} onClick={remove}>
            Delete organization
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-ink-2">
          This stops every agent and permanently deletes the organization
          {typeof org.agentCount === "number" ? ` with its ${org.agentCount} agent${org.agentCount === 1 ? "" : "s"}` : ""}
          {typeof org.openTasks === "number" && org.openTasks ? ` and ${org.openTasks} open task${org.openTasks === 1 ? "" : "s"}` : ""}: conversations, tasks, sessions, the office and its
          objects. Files in its workspace folder on disk are kept. This can&apos;t be undone.
        </p>
        <div>
          <div className="mb-1.5 text-xs text-ink-3">
            Type <b className="text-ink">{org.name}</b> to confirm
          </div>
          <Input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === "Enter" && typed.trim() === org.name && void remove()} />
        </div>
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}
