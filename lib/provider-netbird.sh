#!/usr/bin/env bash
# shellcheck shell=bash
# NetBird (cloud-managed). Outbound-only: management/signal over TLS, peers via ICE + relay fallback.

NB_KEYRING=/usr/share/keyrings/netbird-archive-keyring.gpg
NB_LIST=/etc/apt/sources.list.d/netbird.list

netbird_installed() { command -v netbird >/dev/null 2>&1; }

netbird_install() {
  if netbird_installed; then
    ok "netbird: already installed"
  else
    log "netbird: adding NetBird apt repo"
    add_apt_repo "$NB_KEYRING" "$NB_LIST" \
      https://pkgs.netbird.io/debian/public.key \
      "deb [signed-by=@KEYRING@] https://pkgs.netbird.io/debian stable main" \
      || die "netbird: could not fetch the signing key"
    apt_refresh
    apt_install netbird || die "netbird: apt install failed"
    ok "netbird: installed"
  fi
  # The package normally registers the service; make sure either way.
  netbird service install >/dev/null 2>&1 || true
  netbird service start >/dev/null 2>&1 || true
}

netbird_up() {
  netbird service start >/dev/null 2>&1 || true
  local args=(up)
  if [ -n "${NB_SETUP_KEY:-}" ]; then
    args+=(--setup-key "$NB_SETUP_KEY")
  elif [ ! -t 0 ]; then
    warn "netbird: NB_SETUP_KEY not set and no TTY - run 'sudo netbird up' interactively"
    return 0
  fi
  if [ -n "${NB_MANAGEMENT_URL:-}" ]; then
    args+=(--management-url "$NB_MANAGEMENT_URL")
  fi
  netbird "${args[@]}"
}

netbird_down() { netbird down; }

netbird_state() {
  local out
  command -v netbird >/dev/null 2>&1 || { echo missing; return 0; }
  out=$(netbird status 2>&1) || { echo stopped; return 0; }
  if grep -q 'Management: Connected' <<<"$out"; then echo connected; else echo disconnected; fi
}

netbird_uninstall() {
  netbird_installed && { netbird down >/dev/null 2>&1 || true; }
  apt_remove netbird >/dev/null 2>&1 || true
  rm -f "$NB_LIST" "$NB_KEYRING"
  ok "netbird: removed"
}
