"""Connectors for agents Pantheon doesn't run itself, each in its own protocol, deep enough
that they work like Pantheon's own agents (see app/agents/external.py):

- claude.py: Claude Code, two-way stream-json (the Agent SDK's protocol);
- codex.py: Codex, ``exec --json`` events and session resume;
- acp.py: anything that speaks the Agent Client Protocol (Hermes Agent, Gemini CLI,
  OpenCode, Kimi, and Claude Code or Codex through their ACP adapters);
- check.py: the Check button (installed? signed in? answering? which models?).

The agent-kinds pack (packs/agent-kinds) registers them as the ``cli`` kind's presets.
"""
