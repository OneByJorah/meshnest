#!/usr/bin/env bash
# shellcheck shell=bash
# Cloudflare WARP (WARP Connector mesh). Outbound-only: WARP dials Cloudflare's edge.

WARP_KEYRING=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
WARP_LIST=/etc/apt/sources.list.d/cloudflare-client.list
WARP_SYSCTL=/etc/sysctl.d/99-zzz-cloudflare-warp-connector.conf

warp_installed() { command -v warp-cli >/dev/null 2>&1; }

warp_forwarding() {
  if [ "${WARP_ENABLE_FORWARDING:-1}" != 1 ]; then
    log "warp: skipping forwarding sysctl (WARP_ENABLE_FORWARDING=0)"
    return 0
  fi
  printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\nnet.ipv6.conf.all.accept_ra = 2\n' > "$WARP_SYSCTL"
  sysctl --system >/dev/null
  ok "warp: IP forwarding enabled for connector mode ($WARP_SYSCTL)"
}

warp_install() {
  if warp_installed; then
    ok "warp: cloudflare-warp already installed"
  else
    log "warp: adding Cloudflare apt repo"
    add_apt_repo "$WARP_KEYRING" "$WARP_LIST" \
      https://pkg.cloudflareclient.com/pubkey.gpg \
      "deb [signed-by=@KEYRING@] https://pkg.cloudflareclient.com/ ${OS_CODENAME} main" \
      || die "warp: could not fetch the Cloudflare signing key"
    apt_refresh
    apt_install cloudflare-warp \
      || die "warp: apt install failed (does Cloudflare publish a '${OS_CODENAME}' repo yet?)"
    ok "warp: installed"
  fi
  warp_forwarding
}

warp_up() {
  systemctl enable --now warp-svc >/dev/null 2>&1 || true
  if [ -z "${WARP_CONNECTOR_TOKEN:-}" ]; then
    warn "warp: WARP_CONNECTOR_TOKEN not set in $MN_ETC/mesh.env - installed but not enrolled"
    return 0
  fi
  wait_for 15 warp-cli --accept-tos status || true
  if ! warp-cli --accept-tos connector new "$WARP_CONNECTOR_TOKEN"; then
    warn "warp: 'connector new' failed (already registered? run: warp-cli registration delete)"
  fi
  warp-cli --accept-tos connect
}

warp_down() { warp-cli --accept-tos disconnect; }

warp_state() {
  local out
  command -v warp-cli >/dev/null 2>&1 || { echo missing; return 0; }
  out=$(warp-cli --accept-tos status 2>&1) || { echo stopped; return 0; }
  if grep -qi 'status update: connected' <<<"$out"; then echo connected; else echo disconnected; fi
}

warp_uninstall() {
  warp_installed && { warp-cli --accept-tos disconnect >/dev/null 2>&1 || true; }
  apt_remove cloudflare-warp >/dev/null 2>&1 || true
  rm -f "$WARP_LIST" "$WARP_KEYRING" "$WARP_SYSCTL"
  sysctl --system >/dev/null 2>&1 || true
  ok "warp: removed"
}
