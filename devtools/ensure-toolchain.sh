#!/bin/bash
# Ensure the repository's pinned Node and uv toolchain is present.
#
# Agent sessions start from a bare container: without this, every `make` target,
# `uv` command, and `npm ci` fails until someone installs the toolchain by hand.
# One shared copy serves every agent; Claude Code registers it in
# .claude/settings.json and Codex in .codex/hooks.json. Both invoke it by this
# path, so the script is never duplicated per agent.
#
# Supply-chain policy (see SUPPLY-CHAIN-SECURITY.md): versions are PINNED to the
# repository's own toolchain pins and every download is verified against a pinned
# SHA-256 checksum. Do NOT change this to install "latest": a moving Node major
# breaks the `engines` range that `engine-strict=true` enforces, and resolving
# tool versions at runtime bypasses the 14-day cool-off window.
#
# To bump a pin: change .node-version and .nvmrc (Node) or uv.toml
# required-version (uv), then update the matching version and checksums below.
# `devtools/check_supply_chain.py` fails when those fall out of step, so the
# bump cannot silently half-land.
#
# Checksum sources:
#   Node: https://nodejs.org/dist/v<VERSION>/SHASUMS256.txt
#   uv:   https://github.com/astral-sh/uv/releases/download/<VERSION>/<asset>.sha256

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

# Pinned versions these checksums describe. check_supply_chain.py asserts each
# matches its canonical source (.node-version / uv.toml), so a bump that misses
# this file fails `make verify` instead of drifting.
NODE_VERSION="24.18.0"
UV_VERSION="0.11.26"

node_checksum_for() {
    case "$1" in
        linux-x64) echo "783130984963db7ba9cbd01089eaf2c2efb055c7c1693c943174b967b3050cb8" ;;
        linux-arm64) echo "6b4484c2190274175df9aa8f28e2d758a819cb1c1fe6ab481e2f95b463ab8508" ;;
        darwin-x64) echo "dfd0dbd3e721503434df7b7205e719f61b3a3a31b2bcf9729b8b91fea240f080" ;;
        darwin-arm64) echo "e1a97e14c99c803e96c7339403282ea05a499c32f8d83defe9ef5ec66f979ed1" ;;
        *) echo "" ;;
    esac
}

uv_checksum_for() {
    case "$1" in
        x86_64-unknown-linux-gnu) echo "6426a73c3837e6e2483ee344cbc00f36394d179afcba6183cb77437e67db4af0" ;;
        aarch64-unknown-linux-gnu) echo "befa1a59c91e96eb601b0fd9a97c03dd666f17baba644b2b4db9c59a767e387e" ;;
        x86_64-apple-darwin) echo "922b460202707dd5f4ccacbadbe7f6a546cc46e82a99bf50ca99a7977a78eddd" ;;
        aarch64-apple-darwin) echo "8f7fbf1708399b921857bce71e1d60f0d3ccf52a30caebc1c1a2f175dce13ab6" ;;
        *) echo "" ;;
    esac
}

log() { echo "[toolchain] $*"; }

# A missing toolchain is worth fixing but must not stop the session from
# starting: an offline or egress-restricted sandbox should still open, with the
# manual command in view. Checksum mismatches are the exception and stop hard.
warn_and_continue() {
    log "WARNING: $*"
    log "Install it manually before running make targets; see docs/runbooks/environment-bootstrap.runbook.md."
    exit 0
}

sha256_of() {
    if command -v sha256sum &> /dev/null; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Verify against the pinned checksum before anything is unpacked or executed.
verify_or_die() {
    local archive="$1" expected="$2" actual
    actual="$(sha256_of "$archive")"
    if [ "$actual" != "$expected" ]; then
        log "ERROR: checksum mismatch for $(basename "$archive")"
        log "  expected $expected"
        log "  actual   $actual"
        rm -f "$archive"
        exit 1
    fi
}

# True when $1 is at least $2, comparing dotted versions numerically.
version_at_least() {
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

read_pin() {
    local file="$1" pin=""
    [ -f "$ROOT/$file" ] && pin="$(tr -d '[:space:]' < "$ROOT/$file")"
    echo "$pin"
}

# uv.toml carries a range (">=0.11.26"); the bare version is the floor to accept.
uv_required_floor() {
    sed -n 's/^required-version[[:space:]]*=[[:space:]]*"[^0-9]*\([0-9.]*\)".*/\1/p' \
        "$ROOT/uv.toml" | head -n1
}

detect_platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    [ "$arch" = "x86_64" ] && arch="x64"
    [ "$arch" = "aarch64" ] && arch="arm64"
    echo "${os}-${arch}"
}

ensure_node() {
    # .node-version is the canonical pin; fall back to this file's pin only if
    # the repository file is unreadable.
    local want
    want="$(read_pin .node-version)"
    want="${want:-$NODE_VERSION}"

    if command -v node &> /dev/null; then
        local have want_major have_major
        have="$(node -v 2>/dev/null | sed 's/^v//')"
        want_major="${want%%.*}"
        have_major="${have%%.*}"
        # package.json pairs the pin with an `engines` ceiling of the next major
        # (`>=24.18.0 <25`), and engine-strict makes `npm ci` enforce it.
        if [ "$have_major" = "$want_major" ] && version_at_least "$have" "$want"; then
            log "Node ${have} satisfies the ${want} pin."
            return 0
        fi
        log "Node ${have} does not satisfy the ${want} pin; installing pinned Node."
    else
        log "Node not found; installing pinned v${want}."
    fi

    local platform expected
    platform="$(detect_platform)"
    expected="$(node_checksum_for "$platform")"
    if [ -z "$expected" ] || [ "$want" != "$NODE_VERSION" ]; then
        warn_and_continue "no pinned Node checksum for ${platform} at v${want}"
    fi

    local asset url tmp dest
    asset="node-v${want}-${platform}.tar.gz"
    url="https://nodejs.org/dist/v${want}/${asset}"
    tmp="$(mktemp -d)"
    log "Downloading ${url}"
    if ! curl -fsSL --connect-timeout 20 -o "${tmp}/${asset}" "$url"; then
        rm -rf "$tmp"
        warn_and_continue "could not download Node v${want}"
    fi
    verify_or_die "${tmp}/${asset}" "$expected"
    log "Checksum verified for ${asset}"

    dest="$HOME/.local/lib/nodejs"
    mkdir -p "$dest" "$HOME/.local/bin"
    rm -rf "${dest:?}/node-v${want}-${platform}"
    tar -xzf "${tmp}/${asset}" -C "$dest"
    # Symlink rather than copy so npm keeps resolving its sibling lib/ tree.
    local bindir="${dest}/node-v${want}-${platform}/bin"
    local tool
    for tool in node npm npx; do
        [ -e "${bindir}/${tool}" ] && ln -sf "${bindir}/${tool}" "$HOME/.local/bin/${tool}"
    done
    rm -rf "$tmp"
    log "Installed Node v${want} to ${dest}"

    # Without this, `npm install -g` would install into the unpacked tree's own
    # bin directory, which is not on PATH, so a global tool would install
    # successfully and then not resolve. Point global installs at ~/.local,
    # whose bin directory is already the one this script links into. Only after
    # installing our own Node: an existing satisfying toolchain keeps whatever
    # prefix its owner configured.
    if "${bindir}/npm" config set prefix "$HOME/.local" 2> /dev/null; then
        log "Set the npm global prefix to ~/.local so global tools land on PATH."
    else
        log "NOTE: could not set the npm global prefix; 'npm install -g' may install off PATH."
    fi
}

ensure_uv() {
    local floor
    floor="$(uv_required_floor)"
    floor="${floor:-$UV_VERSION}"

    if command -v uv &> /dev/null; then
        local have
        have="$(uv --version 2>/dev/null | awk '{print $2}')"
        if [ -n "$have" ] && version_at_least "$have" "$floor"; then
            log "uv ${have} satisfies the >=${floor} requirement."
            return 0
        fi
        log "uv ${have:-unknown} is below the >=${floor} requirement; installing pinned uv."
    else
        log "uv not found; installing pinned v${UV_VERSION}."
    fi

    local os arch target
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    [ "$arch" = "arm64" ] && arch="aarch64"
    if [ "$os" = "darwin" ]; then
        target="${arch}-apple-darwin"
    else
        target="${arch}-unknown-linux-gnu"
    fi

    local expected
    expected="$(uv_checksum_for "$target")"
    if [ -z "$expected" ]; then
        warn_and_continue "no pinned uv checksum for ${target}"
    fi

    local asset url tmp
    asset="uv-${target}.tar.gz"
    url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${asset}"
    tmp="$(mktemp -d)"
    log "Downloading ${url}"
    if ! curl -fsSL --connect-timeout 20 -o "${tmp}/${asset}" "$url"; then
        rm -rf "$tmp"
        warn_and_continue "could not download uv v${UV_VERSION}"
    fi
    verify_or_die "${tmp}/${asset}" "$expected"
    log "Checksum verified for ${asset}"

    tar -xzf "${tmp}/${asset}" -C "$tmp"
    mkdir -p "$HOME/.local/bin"
    install -m 0755 "${tmp}/uv-${target}/uv" "$HOME/.local/bin/uv"
    install -m 0755 "${tmp}/uv-${target}/uvx" "$HOME/.local/bin/uvx"
    rm -rf "$tmp"
    log "Installed uv v${UV_VERSION} to ~/.local/bin"
}

ensure_node
ensure_uv

# Later tool calls run in their own shells, so they only see these binaries if
# ~/.local/bin is on PATH there.
case ":${PATH}:" in
    *":$HOME/.local/bin:"*) ;;
    *) log "NOTE: add ~/.local/bin to PATH so these binaries resolve in later commands." ;;
esac

exit 0
