#!/bin/sh
# Install Pantheon Studio on macOS or Linux from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/adityaagarw/Pantheon/main/install.sh | sh
#
# PANTHEON_VERSION=2.0.1 picks a release instead of the latest.
set -eu

REPO="adityaagarw/Pantheon"
say() { printf '%s\n' "$*"; }
fail() { printf 'pantheon: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "this needs $1"; }

need curl
if [ -n "${PANTHEON_VERSION:-}" ]; then
  api="https://api.github.com/repos/$REPO/releases/tags/v${PANTHEON_VERSION#v}"
else
  api="https://api.github.com/repos/$REPO/releases/latest"
fi
urls=$(curl -fsSL "$api" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"/\1/') ||
  fail "couldn't read the release from GitHub ($api)"
[ -n "$urls" ] || fail "couldn't read the release from GitHub ($api)"

pick() {  # the first asset whose name ends with $1
  printf '%s\n' "$urls" | grep -E "$1\$" | head -n 1
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fetch() {  # download $1 into $tmp and print the file's path
  file="$tmp/$(basename "$1")"
  say "Downloading $(basename "$1")..." >&2
  curl -fL --progress-bar "$1" -o "$file" || fail "the download failed"
  printf '%s' "$file"
}
sudo_() { if [ "$(id -u)" -eq 0 ]; then "$@"; else need sudo; sudo "$@"; fi; }

os=$(uname -s)
arch=$(uname -m)
case "$os" in
  Darwin)
    case "$arch" in
      arm64) url=$(pick '_aarch64\.dmg') ;;
      x86_64) url=$(pick '_x64\.dmg') ;;
      *) fail "no Mac build for $arch" ;;
    esac
    [ -n "$url" ] || fail "this release has no Mac installer for $arch"
    dmg=$(fetch "$url")
    mnt="$tmp/mnt"
    mkdir -p "$mnt"
    hdiutil attach -nobrowse -quiet -mountpoint "$mnt" "$dmg"
    app=$(find "$mnt" -maxdepth 1 -name '*.app' | head -n 1)
    [ -n "$app" ] || { hdiutil detach -quiet "$mnt"; fail "the disk image has no app in it"; }
    dest=/Applications
    [ -w "$dest" ] || dest="$HOME/Applications"
    mkdir -p "$dest"
    rm -rf "$dest/$(basename "$app")"
    cp -R "$app" "$dest/"
    hdiutil detach -quiet "$mnt"
    # The app isn't notarized yet; this stops macOS refusing to open a downloaded copy.
    xattr -dr com.apple.quarantine "$dest/$(basename "$app")" 2>/dev/null || true
    say "Installed $(basename "$app") in $dest."
    say "Open it from Launchpad, or: open \"$dest/$(basename "$app")\""
    ;;
  Linux)
    [ "$arch" = x86_64 ] || fail "no Linux build for $arch yet (x86_64 only)"
    if command -v apt-get >/dev/null 2>&1 && [ -n "$(pick '_amd64\.deb')" ]; then
      deb=$(fetch "$(pick '_amd64\.deb')")
      sudo_ apt-get install -y "$deb"
      say "Installed. Start Pantheon Studio from your app menu."
    elif command -v dnf >/dev/null 2>&1 && [ -n "$(pick '\.x86_64\.rpm')" ]; then
      rpm=$(fetch "$(pick '\.x86_64\.rpm')")
      sudo_ dnf install -y "$rpm"
      say "Installed. Start Pantheon Studio from your app menu."
    elif command -v zypper >/dev/null 2>&1 && [ -n "$(pick '\.x86_64\.rpm')" ]; then
      rpm=$(fetch "$(pick '\.x86_64\.rpm')")
      sudo_ zypper --non-interactive install --allow-unsigned-rpm "$rpm"
      say "Installed. Start Pantheon Studio from your app menu."
    else
      url=$(pick '_amd64\.AppImage')
      [ -n "$url" ] || fail "this release has no Linux installer"
      img=$(fetch "$url")
      bin="$HOME/.local/bin"
      mkdir -p "$bin"
      mv "$img" "$bin/pantheon-studio"
      chmod +x "$bin/pantheon-studio"
      say "Installed the AppImage as $bin/pantheon-studio."
      case ":$PATH:" in *":$bin:"*) ;; *) say "Add $bin to your PATH, or run it by its full path." ;; esac
    fi
    ;;
  *)
    fail "unsupported system: $os (on Windows, use install.ps1)"
    ;;
esac
