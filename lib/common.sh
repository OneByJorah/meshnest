#!/usr/bin/env bash
# shellcheck shell=bash
# j1-mesh-stack shared helpers. Sourced by bin/j1-mesh; never executed directly.

: "${JMS_ROOT:?JMS_ROOT must be set by the caller}"

JMS_VERSION="$(tr -d '[:space:]' < "$JMS_ROOT/VERSION" 2>/dev/null || echo dev)"
JMS_ETC="${JMS_ETC:-/etc/j1-mesh}"
JMS_STATE="${JMS_STATE:-/var/lib/j1-mesh}"
ALL_PROVIDERS="warp tailscale netbird zerotier"
# shellcheck disable=SC2034
export JMS_VERSION JMS_ETC JMS_STATE ALL_PROVIDERS

if [ -t 1 ]; then
  C_AMB=$'\033[38;5;214m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_AMB=""; C_GRN=""; C_YEL=""; C_RED=""; C_OFF=""
fi

log()  { printf '%s▌%s %s\n' "$C_AMB" "$C_OFF" "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

# Sets REPO_DISTRO (debian|ubuntu) and OS_CODENAME. Handles Mint/Pop!_OS/etc. via ID_LIKE.
os_detect() {
  [ -r /etc/os-release ] || die "Cannot read /etc/os-release."
  command -v apt-get >/dev/null 2>&1 || die "apt-get not found. Debian/Ubuntu-family only for now (see README roadmap)."
  local id like codename ubuntu_codename
  id=$(. /etc/os-release; printf '%s' "${ID:-}")
  like=$(. /etc/os-release; printf '%s' "${ID_LIKE:-}")
  codename=$(. /etc/os-release; printf '%s' "${VERSION_CODENAME:-}")
  ubuntu_codename=$(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-}")
  case "$id" in
    debian|ubuntu) REPO_DISTRO="$id" ;;
    *)
      case " $like " in
        *" ubuntu "*) REPO_DISTRO=ubuntu; codename="${ubuntu_codename:-$codename}" ;;
        *" debian "*) REPO_DISTRO=debian ;;
        *) die "Unsupported distro '$id'. Debian/Ubuntu-family only for now." ;;
      esac
      ;;
  esac
  [ -n "$codename" ] || die "Could not determine the release codename from /etc/os-release."
  OS_CODENAME="$codename"
  # shellcheck disable=SC2034
  export REPO_DISTRO OS_CODENAME
}

apt_refresh() { apt-get update -qq; }
apt_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"; }
apt_remove()  { DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq "$@"; }

# add_apt_repo <keyring-path> <sources-list-path> <key-url> <deb-line containing @KEYRING@>
# Accepts armored or binary keys. Returns non-zero if the key cannot be fetched.
add_apt_repo() {
  local keyring=$1 list=$2 url=$3 line=$4 tmp
  tmp=$(mktemp)
  if ! curl -fsSL "$url" -o "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if grep -q 'BEGIN PGP' "$tmp"; then
    gpg --yes --dearmor -o "$keyring" "$tmp"
  else
    install -m 0644 "$tmp" "$keyring"
  fi
  rm -f "$tmp"
  chmod 0644 "$keyring"
  printf '%s\n' "${line//@KEYRING@/$keyring}" > "$list"
}

load_env() {
  local f="$JMS_ETC/mesh.env"
  if [ -r "$f" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$f"
    set +a
  fi
}

# resolve_providers [name...] -> space-separated, validated list (all providers when none given)
resolve_providers() {
  local p out=""
  if [ "$#" -eq 0 ]; then
    printf '%s\n' "$ALL_PROVIDERS"
    return 0
  fi
  for p in "$@"; do
    case " $ALL_PROVIDERS " in
      *" $p "*) out="$out $p" ;;
      *) die "Unknown provider '$p' (choose from: $ALL_PROVIDERS)" ;;
    esac
  done
  printf '%s\n' "${out# }"
}

# Wait up to N seconds for a command to succeed.
wait_for() {
  local secs=$1; shift
  local i
  for ((i = 0; i < secs; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}
