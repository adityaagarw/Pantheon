/**
 * One command line for everything (Ctrl/⌘ K): go anywhere, open any pane, find any agent,
 * run an action, or type "@Mira fix the layout" to send that straight to Mira.
 */
import { Command } from "cmdk";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "@/lib/api";
import type { Org } from "@/lib/types";
import { useOrg } from "@/store/org";
import { Icon, type IconName } from "./icons";
import { agentHue, hsl, stateLook } from "./look";
import { PANES } from "./panes";
import { useStudio } from "./store";

const PLACES: { label: string; path: string; icon: IconName }[] = [
  { label: "Home", path: "/", icon: "home" },
  { label: "Zeus, the supervisor", path: "/supervisor", icon: "zeus" },
  { label: "Argus, oversight", path: "/argus", icon: "argus" },
  { label: "Kronos, the host", path: "/kronos", icon: "kronos" },
  { label: "Packs: libraries, assets, plugins", path: "/packs", icon: "packs" },
  { label: "Benchmarks", path: "/benchmarks", icon: "bench" },
  { label: "Settings", path: "/settings", icon: "settings" },
];

export function openPane(pane: string) {
  window.dispatchEvent(new CustomEvent("studio:open-pane", { detail: pane }));
}

export function CommandBar() {
  const open = useStudio((s) => s.commandOpen);
  const setOpen = useStudio((s) => s.setCommand);
  const select = useStudio((s) => s.select);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const inOrg = pathname.startsWith("/org/");
  const org = useOrg((s) => s.org);
  const agentsById = useOrg((s) => s.agents);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [search, setSearch] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!useStudio.getState().commandOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  useEffect(() => {
    if (!open) return;
    setSearch("");
    setSent(null);
    api.orgs().then(setOrgs).catch(() => {});
  }, [open]);

  const agents = useMemo(
    () => (inOrg ? Object.values(agentsById).filter((a) => !a.isSupervisor && !a.metaRole) : []),
    [agentsById, inOrg],
  );

  // "@Name the message": the agent whose name the text starts with, and the rest.
  const mention = useMemo(() => {
    const m = /^@(\S+)\s+([\s\S]+)$/.exec(search.trim());
    if (!m || !org) return null;
    const who = agents.find((a) => a.name.toLowerCase() === m[1]!.toLowerCase())
      ?? agents.find((a) => a.name.toLowerCase().startsWith(m[1]!.toLowerCase()));
    return who ? { agent: who, text: m[2]! } : null;
  }, [search, agents, org]);

  const run = (fn: () => void) => {
    fn();
    setOpen(false);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[1000] flex items-start justify-center bg-black/45 pt-[12vh] backdrop-blur-[2px]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}
        >
          <motion.div
            initial={{ opacity: 0, y: -14, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 520, damping: 36 }}
            className="glass st-cmdk w-[min(680px,92vw)] overflow-hidden rounded-2xl"
            style={{ background: "rgba(16,19,32,0.92)" }}
          >
            <Command
              label="Command bar"
              loop
              shouldFilter={!mention}
              // "@th" finds agents by name, as the start of a message to them.
              filter={(value, query) => {
                const q = query.replace(/^@/, "").toLowerCase().trim();
                if (!q) return 1;
                if (query.startsWith("@")) return value.startsWith("agent ") && value.slice(6).toLowerCase().startsWith(q) ? 1 : 0;
                return value.toLowerCase().includes(q) ? 1 : 0;
              }}
            >
              <Command.Input
                autoFocus
                value={search}
                onValueChange={setSearch}
                placeholder={inOrg ? "Go to, open, find… or @Agent tell them something" : "Go to, open, find…"}
                onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
              />
              <Command.List>
                {sent && <div className="px-4 py-3 text-sm text-[var(--st-green)]">{sent}</div>}
                {mention && (
                  <Command.Group heading="Send">
                    <Command.Item
                      value={`send ${mention.agent.name}`}
                      onSelect={async () => {
                        await api.send(org!.id, { to: mention.agent.id, content: mention.text });
                        select(mention.agent.id);
                        openPane("console");
                        setSent(`Sent to ${mention.agent.name}.`);
                        setTimeout(() => setOpen(false), 500);
                      }}
                    >
                      <Icon name="send" size={16} />
                      <span>
                        Send to <b style={{ color: hsl(agentHue(mention.agent), 75, 80) }}>{mention.agent.name}</b>:{" "}
                        <span className="text-[var(--st-ink-3)]">{mention.text.slice(0, 80)}</span>
                      </span>
                    </Command.Item>
                  </Command.Group>
                )}
                <Command.Empty>Nothing matches. Start with @ and an agent's name to message them.</Command.Empty>
                {inOrg && agents.length > 0 && (
                  <Command.Group heading="Agents">
                    {agents.map((a) => {
                      const look = stateLook(a.runtimeStatus, a.status);
                      return (
                        <Command.Item
                          key={a.id}
                          value={`agent ${a.name} ${a.role}`}
                          onSelect={() => {
                            // Picked while writing "@na…": finish the name and keep typing.
                            if (search.startsWith("@")) {
                              setSearch(`@${a.name} `);
                              return;
                            }
                            run(() => {
                              select(a.id);
                              openPane("console");
                            });
                          }}
                        >
                          <span className="size-2.5 rounded-full" style={{ background: hsl(agentHue(a), 80, 65) }} />
                          <span className="text-[var(--st-ink)]">{a.name}</span>
                          <span className="truncate text-[var(--st-ink-3)]">{a.role}</span>
                          <span className="ml-auto text-[12px]" style={{ color: look.color }}>{look.label}</span>
                        </Command.Item>
                      );
                    })}
                  </Command.Group>
                )}
                {inOrg && (
                  <Command.Group heading="Panes">
                    {PANES.map((p) => (
                      <Command.Item key={p.id} value={`pane ${p.title} ${p.hint}`} onSelect={() => run(() => openPane(p.id))}>
                        <Icon name={p.icon} size={16} />
                        <span className="text-[var(--st-ink)]">{p.title}</span>
                        <span className="truncate text-[var(--st-ink-3)]">{p.hint}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
                {inOrg && org && (
                  <Command.Group heading="This org">
                    <Command.Item
                      value={org.status === "paused" ? "resume org" : "pause org"}
                      onSelect={() => run(() => {
                        api.updateOrg(org.id, { status: org.status === "paused" ? "running" : "paused" })
                          .then((o) => useOrg.getState().setOrg(o));
                      })}
                    >
                      <Icon name={org.status === "paused" ? "play" : "pause"} size={16} />
                      {org.status === "paused" ? "Resume all agents" : "Pause all agents"}
                    </Command.Item>
                  </Command.Group>
                )}
                <Command.Group heading="Organizations">
                  {orgs.map((o) => (
                    <Command.Item key={o.id} value={`org ${o.name}`} onSelect={() => run(() => navigate(`/org/${o.id}`))}>
                      <span className="size-2.5 rounded-full" style={{ background: hsl(agentHue({ name: o.name, avatar: {} }), 70, 62) }} />
                      <span className="text-[var(--st-ink)]">{o.name}</span>
                      {(o.workingAgents ?? 0) > 0 && <span className="ml-auto text-[12px] text-[var(--st-violet)]">{o.workingAgents} working</span>}
                    </Command.Item>
                  ))}
                </Command.Group>
                <Command.Group heading="Go to">
                  {PLACES.map((p) => (
                    <Command.Item key={p.path} value={`go ${p.label}`} onSelect={() => run(() => navigate(p.path))}>
                      <Icon name={p.icon} size={16} /> {p.label}
                    </Command.Item>
                  ))}
                </Command.Group>
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
