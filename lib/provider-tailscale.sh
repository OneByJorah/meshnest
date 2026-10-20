#!/usr/bin/env bash
# shellcheck shell=bash
# Tailscale. Outbound-only: coordination over HTTPS, peers via NAT traversal or DERP relays.

TS_KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
TS_LIST=/etc/apt/sources.list.d/tailscale.list

tailscale_installed() { command -v tailscale >/dev/null 2>&1; }

tailscale_install() {
  if tailscale_installed; then
    ok "tailscale: already installed"
  else
    log "tailscale: adding Tailscale apt repo"
    add_apt_repo "$TS_KEYRING" "$TS_LIST" \
      "https://pkgs.tailscale.com/stable/${REPO_DISTRO}/${OS_CODENAME}.noarmor.gpg" \
      "deb [signed-by=@KEYRING@] https://pkgs.tailscale.com/stable/${REPO_DISTRO} ${OS_CODENAME} main" \
      || die "tailscale: could not fetch the signing key for ${REPO_DISTRO}/${OS_CODENAME}"
    apt_refresh
    apt_install tailscale || die "tailscale: apt install failed"
    ok "tailscale: installed"
  fi
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
}

tailscale_up() {
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
  wait_for 15 tailscale version || true
  local args=(up --hostname "${MESH_HOSTNAME:-$(hostname -s)}")
  if [ -n "${TS_AUTHKEY:-}" ]; then
    args+=(--authkey "$TS_AUTHKEY")
  elif [ ! -t 0 ]; then
    warn "tailscale: TS_AUTHKEY not set and no TTY - run 'sudo tailscale up' interactively"
    return 0
  fi
  if [ -n "${TS_EXTRA_ARGS:-}" ]; then
    # shellcheck disable=SC2206
    args+=($TS_EXTRA_ARGS)
  fi
  tailscale "${args[@]}"
}

tailscale_down() { tailscale down; }

tailscale_state() {
  command -v tailscale >/dev/null 2>&1 || { echo missing; return 0; }
  systemctl is-active --quiet tailscaled 2>/dev/null || { echo stopped; return 0; }
  if tailscale status >/dev/null 2>&1; then echo connected; else echo disconnected; fi
}

tailscale_uninstall() {
  tailscale_installed && { tailscale down >/dev/null 2>&1 || true; }
  apt_remove tailscale >/dev/null 2>&1 || true
  rm -f "$TS_LIST" "$TS_KEYRING"
  ok "tailscale: removed"
}
