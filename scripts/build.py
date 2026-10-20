"""Build Pantheon Studio's installers on this computer, without CI.

    python scripts/build.py                  # installers for this system
    python scripts/build.py --linux          # also Linux (.deb, .rpm, AppImage), in Docker
    python scripts/build.py --linux --check  # and install and start each one to check it
    python scripts/build.py --release        # and upload them to the GitHub release v<version>

What it makes, by system:

    Windows  NSIS setup (.exe)
    macOS    .app and .dmg, for this Mac's chip (Apple's tools only run on a Mac)
    Linux    .deb, .rpm and AppImage; on Windows or a Mac with --linux, built in an
             Ubuntu 22.04 container (Docker), the same base the release workflow uses

Everything lands in dist/<version>/: one folder per platform as built, and release/ with
the files renamed for a GitHub release (and latest.json for the in-app updater when the
build was signed). Builds are signed for updates when TAURI_SIGNING_PRIVATE_KEY is set or
~/.tauri/pantheon-studio.key exists.

Needs: Node 22, Rust, uv (and Docker for --linux). The first Linux build downloads and
compiles a lot; later ones reuse the cache volumes (pantheon-build-*).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
TAURI = DESKTOP / "src-tauri"
BUNDLE = TAURI / "target" / "release" / "bundle"
KEY_FILE = Path.home() / ".tauri" / "pantheon-studio.key"
WINDOWS = sys.platform == "win32"


def say(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def run(cmd: list[str] | str, cwd: Path = ROOT, env: dict | None = None, shell: bool = False) -> None:
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"+ {printable}", flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True, shell=shell or (WINDOWS and not isinstance(cmd, str)
                                                                     and cmd[0] in ("npm", "npx")))


def version() -> str:
    return json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))["version"]


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    osname = "windows" if WINDOWS else "darwin" if sys.platform == "darwin" else "linux"
    return f"{osname}-{arch}"


def tools_env(env: dict[str, str]) -> dict[str, str]:
    """Find the build tools, adding rustup's and uv's usual folders to PATH when a fresh
    install hasn't reached this shell's PATH yet; stop with what's missing."""
    extra = [Path.home() / ".cargo" / "bin", Path.home() / ".local" / "bin"]
    path = env.get("PATH", "")
    for d in extra:
        if d.is_dir() and str(d) not in path:
            path = f"{path}{os.pathsep}{d}"
    env["PATH"] = os.environ["PATH"] = path  # every step, not only the Tauri build
    missing = [(t, how) for t, how in (
        ("node", "Node 22: https://nodejs.org"),
        ("npm", "Node 22: https://nodejs.org"),
        ("cargo", "Rust: https://rustup.rs"),
        ("uv", "uv: https://docs.astral.sh/uv/"),
    ) if shutil.which(t, path=path) is None]
    if missing:
        sys.exit("Missing build tools:\n" + "\n".join(f"  {t}  (install {how})" for t, how in missing))
    return env


def signing_env() -> dict[str, str]:
    """The environment for a build, with the update-signing key when there is one."""
    env = tools_env(dict(os.environ))
    if not env.get("TAURI_SIGNING_PRIVATE_KEY") and KEY_FILE.is_file():
        env["TAURI_SIGNING_PRIVATE_KEY"] = KEY_FILE.read_text(encoding="utf-8").strip()
    env.setdefault("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
    return env


def build_here(env: dict[str, str]) -> None:
    say(f"Interface")
    run(["npm", "ci", "--prefix", "ui"])
    run(["npm", "--prefix", "ui", "run", "build"])
    say("Engine runtime (a standalone Python with the engine)")
    run(["uv", "run", "--no-project", "--python", "3.12", "scripts/bundle_engine.py"])
    say("Installers")
    run(["npm", "ci", "--prefix", "apps/desktop"])
    args = ["npx", "tauri", "build", "--config", "src-tauri/tauri.bundle.json"]
    if env.get("TAURI_SIGNING_PRIVATE_KEY"):
        args += ["--config", "src-tauri/tauri.updater.json"]
    if sys.platform == "darwin":
        args += ["--bundles", "app"]  # the .dmg comes from make_dmg.py (no Finder scripting)
    run(args, cwd=DESKTOP, env=env)
    if sys.platform == "darwin":
        run([sys.executable, "scripts/make_dmg.py"])


def collect(into: Path) -> None:
    """Copy this build's installers (and update signatures) into a platform folder."""
    into.mkdir(parents=True, exist_ok=True)
    patterns = ["nsis/*.exe", "nsis/*.sig", "dmg/*.dmg", "macos/*.tar.gz", "macos/*.sig",
                "deb/*.deb", "rpm/*.rpm", "appimage/*.AppImage", "appimage/*.sig"]
    current = version()
    for pattern in patterns:
        for f in BUNDLE.glob(pattern):
            found = re.search(r"\d+\.\d+\.\d+", f.name)
            if found and found.group(0) != current:
                continue  # left from an earlier version's build
            shutil.copy2(f, into / f.name)
            print(f"  {into.name}/{f.name}")


def build_linux(env: dict[str, str], out: Path, check: bool) -> None:
    """Linux packages in an Ubuntu 22.04 container; the sources are copied in (the repo is
    mounted read-only), the packages copied out to `out`."""
    if shutil.which("docker") is None:
        sys.exit("--linux needs Docker")
    out.mkdir(parents=True, exist_ok=True)
    signed = bool(env.get("TAURI_SIGNING_PRIVATE_KEY"))
    script = f"""
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq curl ca-certificates build-essential git sudo file xvfb xauth libfuse2 \\
  libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf libxdo-dev >/dev/null
command -v node >/dev/null || (curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null \\
  && apt-get install -y -qq nodejs >/dev/null)
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
command -v cargo >/dev/null || curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null
export PATH=$HOME/.local/bin:$HOME/.cargo/bin:$PATH
mkdir -p /w && cd /w
tar -C /src --exclude=node_modules --exclude=./apps/desktop/src-tauri/target \\
  --exclude=./apps/desktop/src-tauri/runtime --exclude=.venv --exclude=./build --exclude=./dist \\
  --exclude=./ui/dist --exclude=.git -cf - . | tar -xf -
npm ci --prefix ui && npm --prefix ui run build
uv run --no-project --python 3.12 scripts/bundle_engine.py
npm ci --prefix apps/desktop
cd apps/desktop && npx tauri build --config src-tauri/tauri.bundle.json {'--config src-tauri/tauri.updater.json' if signed else ''}
cd /w
for f in apps/desktop/src-tauri/target/release/bundle/deb/*.deb apps/desktop/src-tauri/target/release/bundle/rpm/*.rpm \\
         apps/desktop/src-tauri/target/release/bundle/appimage/*.AppImage apps/desktop/src-tauri/target/release/bundle/appimage/*.sig; do
  [ -e "$f" ] && cp "$f" /out/
done
{'xvfb-run -a uv run --no-project --python 3.12 scripts/smoke_installer.py' if check else 'true'}
"""
    say("Linux packages (in Docker, Ubuntu 22.04)")
    # --init: xvfb-run (the check) waits for a signal that PID 1 wouldn't get
    cmd = ["docker", "run", "--rm", "--init",
           "-v", f"{ROOT}:/src:ro", "-v", f"{out.resolve()}:/out",
           "-v", "pantheon-build-cargo:/root/.cargo", "-v", "pantheon-build-rustup:/root/.rustup",
           "-v", "pantheon-build-target:/w/apps/desktop/src-tauri/target",
           "-v", "pantheon-build-npm:/root/.npm", "-v", "pantheon-build-uv:/root/.cache/uv",
           "-e", "TAURI_SIGNING_PRIVATE_KEY", "-e", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
           "ubuntu:22.04", "bash", "-c", script]
    denv = dict(env)
    denv["MSYS_NO_PATHCONV"] = "1"  # Git Bash on Windows: keep /src and /out as they are
    run(cmd, env=denv)
    for f in sorted(out.iterdir()):
        print(f"  {out.name}/{f.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--linux", action="store_true", help="also build the Linux packages (in Docker)")
    ap.add_argument("--linux-only", action="store_true", help="only the Linux packages (in Docker)")
    ap.add_argument("--check", action="store_true", help="install and start what was built")
    ap.add_argument("--release", action="store_true",
                    help="upload to the GitHub release v<version> (created as a draft if missing)")
    a = ap.parse_args()

    v = version()
    dist = ROOT / "dist" / v
    env = signing_env()
    started = time.time()
    print(f"Pantheon Studio {v}; update signing: "
          f"{'on' if env.get('TAURI_SIGNING_PRIVATE_KEY') else 'off (no key)'}")

    if not a.linux_only:
        build_here(env)
        collect(dist / "artifacts" / platform_key())
        if a.check:
            say("Checking the installer (install, start, uninstall)")
            run([sys.executable, "scripts/smoke_installer.py"])
    if (a.linux or a.linux_only) and not sys.platform.startswith("linux"):
        build_linux(env, dist / "artifacts" / "linux-x86_64", a.check)

    say("Release files")
    release = dist / "release"
    shutil.rmtree(release, ignore_errors=True)
    run([sys.executable, "scripts/release_files.py", str(dist / "artifacts"), str(release),
         "--tag", f"v{v}", "--repo", "adityaagarw/Pantheon"])

    if a.release:
        say(f"Uploading to the GitHub release v{v}")
        files = [str(f) for f in sorted(release.iterdir())]
        exists = subprocess.run(["gh", "release", "view", f"v{v}"], cwd=ROOT,
                                capture_output=True).returncode == 0
        if exists:
            run(["gh", "release", "upload", f"v{v}", *files, "--clobber"])
        else:
            run(["gh", "release", "create", f"v{v}", *files, "--draft",
                 "--title", f"Pantheon Studio {v}", "--generate-notes"])
        print("Publish the draft on GitHub when you're happy with it.")

    print(f"\nDone in {(time.time() - started) / 60:.0f} min. Installers: {release}")


if __name__ == "__main__":
    main()
