"""Process-wide settings (env vars prefixed ``PANTHEON_``)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PANTHEON_", extra="ignore")

    database_url: str = "postgresql+asyncpg://pantheon:pantheon@localhost:5433/pantheon"
    # Run Alembic migrations on startup (the Docker image does this too).
    auto_migrate: bool = True

    data_dir: str = "../data"
    fernet_key_path: str = "../data/secrets/pantheon.key"

    # Host directories agents may work in. Every org workspace must resolve
    # inside one of these roots; agents can never escape them. Separate
    # multiple roots with ``;`` (Windows) or ``:`` is not supported — use ``;``.
    workspace_roots: str = "../data/workspaces"

    # --- runtime -----------------------------------------------------------
    # Concurrent agent turns across the whole process.
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
    voice_share_dir: str = "../data/voice"
    voice_share_host_dir: str = ""

    # --- extensibility --------------------------------------------------------
    # Plugin folders (see app/plugins.py). Relative paths are from the server dir.
    plugins_dir: str = "../plugins"
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

    # --- computer use & browser ----------------------------------------------
    # cua computer-server in the sandbox desktop container (docker compose --profile computer).
    computer_url: str = "http://127.0.0.1:8711"
    computer_api_key: str = ""
    computer_container: str = ""
    # How long an agent keeps the desktop after its last action (others wait).
    computer_lease_seconds: int = 120
    # Where the backend's own browser reaches Stage pages (for stage_check).
    stage_base_url: str = "http://127.0.0.1:8710"

    # --- web ----------------------------------------------------------------
    cors_origins: str = "http://localhost:3710,http://127.0.0.1:3710"

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
    def plugins_path(self) -> Path:
        return Path(self.plugins_dir).expanduser().resolve()

    @property
    def workspace_root_paths(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.workspace_roots.split(";") if p.strip()]


settings = Settings()
