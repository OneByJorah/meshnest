#!/usr/bin/env bash
# shellcheck shell=bash
# ZeroTier. Outbound-only in practice: peers hole-punch via the planet roots; relay fallback if that fails.

ZT_KEYRING=/usr/share/keyrings/zerotier-archive-keyring.gpg
ZT_LIST=/etc/apt/sources.list.d/zerotier.list
ZT_KEY_URL='https://raw.githubusercontent.com/zerotier/ZeroTierOne/main/doc/contact%40zerotier.com.gpg'

zerotier_installed() { command -v zerotier-cli >/dev/null 2>&1; }

zerotier_install() {
  if zerotier_installed; then
    ok "zerotier: already installed"
  else
    log "zerotier: adding ZeroTier apt repo"
    if add_apt_repo "$ZT_KEYRING" "$ZT_LIST" "$ZT_KEY_URL" \
         "deb [signed-by=@KEYRING@] https://download.zerotier.com/debian/${OS_CODENAME} ${OS_CODENAME} main" \
       && apt_refresh && apt_install zerotier-one; then
      ok "zerotier: installed from the apt repo"
    else
      warn "zerotier: apt repo unavailable for '${OS_CODENAME}'; using ZeroTier's own GPG-verifying installer"
      rm -f "$ZT_LIST" "$ZT_KEYRING"
      apt_refresh || true
      curl -fsSL https://install.zerotier.com | bash || die "zerotier: installation failed"
    fi
  fi
  systemctl enable --now zerotier-one >/dev/null 2>&1 || true
}

zerotier_up() {
  systemctl enable --now zerotier-one >/dev/null 2>&1 || true
  wait_for 20 zerotier-cli info || { warn "zerotier: daemon not answering yet"; return 0; }
  if [ -z "${ZT_NETWORK_ID:-}" ]; then
    warn "zerotier: ZT_NETWORK_ID not set in $MN_ETC/mesh.env - installed but no network joined"
    return 0
  fi
  local id
  # shellcheck disable=SC2086
  for id in ${ZT_NETWORK_ID//,/ }; do
    if zerotier-cli join "$id" >/dev/null; then
      ok "zerotier: joined $id (authorize this node in ZeroTier Central if the network is private)"
    else
      warn "zerotier: failed to join $id"
    fi
  done
}

zerotier_down() {
  local id
  # shellcheck disable=SC2086
  for id in ${ZT_NETWORK_ID//,/ }; do
    zerotier-cli leave "$id" >/dev/null || true
  done
}

zerotier_state() {
  local out
  command -v zerotier-cli >/dev/null 2>&1 || { echo missing; return 0; }
  out=$(zerotier-cli listnetworks 2>&1) || { echo stopped; return 0; }
  if grep -qE ' OK ' <<<"$out"; then echo connected; else echo disconnected; fi
}

zerotier_uninstall() {
  apt_remove zerotier-one >/dev/null 2>&1 || true
  rm -f "$ZT_LIST" "$ZT_KEYRING"
  ok "zerotier: removed"
}
