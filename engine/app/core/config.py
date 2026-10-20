"""Process-wide settings (env vars prefixed ``PANTHEON_``)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ID = "ai.pantheon.studio"  # the desktop app's identifier (tauri.conf.json)


def user_data_dir() -> Path:
    """Where the app keeps its data on this platform: the same folder the desktop app uses
    (Tauri's app data dir), so running the engine alone finds the same orgs."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / APP_ID
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / APP_ID


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PANTHEON_", extra="ignore")

    # Everything the app stores lives under data_dir. The paths below that are left empty
    # are placed inside it.
    data_dir: str = str(user_data_dir())
    database_url: str = ""  # default: SQLite at <data_dir>/pantheon.db
    # Create and upgrade the database schema on startup.
    auto_migrate: bool = True
    # A task that opens a second transaction while it holds one waits for itself (SQLite
    # has one writer). Strict (tests): fail at once with where it happened. Otherwise it
    # is logged and the wait times out.
    db_strict: bool = False
    # The offline mock model makes an org look busy (agents relay work to each other),
    # for building and showing the interface without a real model.
    mock_demo: bool = False
    fernet_key_path: str = ""  # default: <data_dir>/secrets/pantheon.key
    # Where the key that encrypts provider keys and secrets lives: "keychain" (the OS's:
    # Windows Credential Manager, macOS Keychain, Linux Secret Service; the file is moved
    # there) or "file" (fernet_key_path). Without a keychain, the file is used.
    key_store: str = "keychain"

    # Host directories agents may work in. Every org workspace must resolve
    # inside one of these roots; agents can never escape them. Separate
    # multiple roots with ``;`` (Windows) or ``:`` is not supported — use ``;``.
    workspace_roots: str = ""  # default: <data_dir>/workspaces

    # The engine's port on 127.0.0.1 (v1 used 8710; both can run side by side).
    port: int = 8720

    # --- models ------------------------------------------------------------
    # What an agent gets when neither it nor its organization says otherwise.
    # Temperature is never defaulted: unset means the provider's own default.
    default_max_tokens: int = 131072
    default_reasoning_effort: str = "xhigh"  # off | low | medium | high | xhigh

    # --- runtime -----------------------------------------------------------
    # Concurrent agent turns across the whole process. A local server batching requests
    # keeps gaining combined throughput up to about 8 at once (benchmarked: ~600 tokens/s
    # at 8); Settings or the server's own limit can lower it.
    max_concurrent_turns: int = 8
    # Dispatcher safety poll (seconds); wakeups are normally event-driven.
    dispatch_poll_seconds: float = 2.0
    # A claimed delivery whose worker vanished is reclaimed after this lease.
    delivery_lease_seconds: int = 120
    # Consecutive failed turns before an agent parks in ERROR (needs a retry).
    max_turn_failures: int = 5

    # --- tools -------------------------------------------------------------
    tool_output_cap_chars: int = 12_000
    tool_record_cap_chars: int = 200_000
    shell_timeout_seconds: int = 120
    mcp_call_timeout_seconds: int = 120

    # --- voice (audio.cpp, OpenAI-compatible) --------------------------------
    voice_base_url: str = "http://127.0.0.1:8080"
    voice_stt_model: str = ""
    voice_tts_model: str = ""
    voice_vad_model: str = "silero-vad"
    # audio.cpp's VAD reads audio from files on ITS machine. Pantheon writes
    # short clips into `voice_share_dir`; audio.cpp reads the same folder at
    # `voice_share_host_dir` (leave empty when both run on the same filesystem).
    voice_share_dir: str = ""  # default: <data_dir>/voice
    voice_share_host_dir: str = ""

    # --- extensibility --------------------------------------------------------
    # Pack folders (see app/packs.py): the user's own, and the ones that ship with the
    # app (the repo's packs/ folder in development).
    packs_dir: str = ""  # default: <data_dir>/packs
    bundled_packs_dir: str = str(Path(__file__).resolve().parents[3] / "packs")
    # Largest 3D model Zeus may download with import_asset.
    max_asset_bytes: int = 30_000_000
    # How often Argus checks the organizations it's been asked to watch.
    argus_tick_seconds: int = 60

    # --- memory ---------------------------------------------------------------
    # Embeddings for semantic memory search: "local" (fastembed, runs on CPU; the
    # model downloads once into data/models), "off" (keyword search only).
    embeddings: str = "local"
    embeddings_model: str = "BAAI/bge-small-en-v1.5"

    # --- uploads ---------------------------------------------------------------
    max_upload_bytes: int = 25_000_000
    max_video_bytes: int = 500_000_000  # recordings run large; models get a small copy

    # --- computer use & browser ----------------------------------------------
    # cua computer-server in the sandbox desktop container (docker compose --profile computer).
    computer_url: str = "http://127.0.0.1:8711"
    computer_api_key: str = ""
    computer_container: str = ""
    # Stage notebooks run in a kernel inside the sandbox desktop. Empty: the desktop's
    # host on port 8890 (in Docker), or 127.0.0.1:8712 when the computer is reached locally.
    notebook_kernel_url: str = ""
    # How long an agent keeps the desktop after its last action (others wait).
    computer_lease_seconds: int = 120
    # Where the backend's own browser reaches Stage pages (for stage_check).
    stage_base_url: str = ""  # default: http://127.0.0.1:<port>
    # Where the user's browser reaches this backend (links to packaged Stage sites).
    public_url: str = ""  # default: http://localhost:<port>

    # --- web ----------------------------------------------------------------
    # The desktop window (tauri://localhost, or http://tauri.localhost on Windows) and the
    # UI dev server.
    cors_origins: str = ("tauri://localhost,http://tauri.localhost,https://tauri.localhost,"
                         "http://localhost:1420,http://127.0.0.1:1420")

    @model_validator(mode="after")
    def _place_in_data_dir(self) -> Settings:
        data = Path(self.data_dir).expanduser().resolve()
        defaults = {
            "database_url": f"sqlite+aiosqlite:///{(data / 'pantheon.db').as_posix()}",
            "fernet_key_path": str(data / "secrets" / "pantheon.key"),
            "workspace_roots": str(data / "workspaces"),
            "voice_share_dir": str(data / "voice"),
            "packs_dir": str(data / "packs"),
            "stage_base_url": f"http://127.0.0.1:{self.port}",
            "public_url": f"http://localhost:{self.port}",
        }
        for name, value in defaults.items():
            if not getattr(self, name):
                setattr(self, name, value)
        return self

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def artifacts_path(self) -> Path:
        return self.data_path / "artifacts"

    @property
    def uploads_path(self) -> Path:
        return self.data_path / "uploads"

    @property
    def assets_path(self) -> Path:
        return self.data_path / "assets"

    @property
    def packs_path(self) -> Path:
        return Path(self.packs_dir).expanduser().resolve()

    @property
    def bundled_packs_path(self) -> Path:
        return Path(self.bundled_packs_dir).expanduser().resolve()

    @property
    def workspace_root_paths(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.workspace_roots.split(";") if p.strip()]


settings = Settings()
