#!/usr/bin/env bash
# j1-mesh-stack bootstrap installer.
#
#   curl -fsSL https://raw.githubusercontent.com/OneByJorah/j1-mesh-stack/main/install.sh | sudo bash
#   curl -fsSL .../install.sh | sudo bash -s -- tailscale netbird      # pick providers
#   curl -fsSL .../install.sh | sudo JMS_REF=v0.1.0 bash               # pin a release tag
#
# Environment:
#   JMS_REPO    GitHub owner/repo        (default OneByJorah/j1-mesh-stack)
#   JMS_REF     branch or tag            (default main)
#   JMS_DEST    install directory        (default /opt/j1-mesh-stack)
#   JMS_SHA256  expected tarball sha256  (optional integrity pin)
#
# It downloads the repo tarball, unpacks it, links /usr/local/bin/j1-mesh and runs `j1-mesh install`.
# Read it first if you like - it is short, and it opens no ports.
set -euo pipefail

REPO="${JMS_REPO:-OneByJorah/j1-mesh-stack}"
REF="${JMS_REF:-main}"
DEST="${JMS_DEST:-/opt/j1-mesh-stack}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: curl -fsSL <url> | sudo bash" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v tar  >/dev/null 2>&1 || { echo "tar is required" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "▌ Fetching $REPO@$REF"
curl -fsSL "https://github.com/${REPO}/archive/${REF}.tar.gz" -o "$tmp/src.tar.gz"

if [ -n "${JMS_SHA256:-}" ]; then
  echo "${JMS_SHA256}  $tmp/src.tar.gz" | sha256sum -c - >/dev/null \
    || { echo "SHA-256 mismatch - aborting" >&2; exit 1; }
fi

mkdir -p "$tmp/src"
tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1
[ -x "$tmp/src/bin/j1-mesh" ] || { echo "Unexpected archive layout" >&2; exit 1; }

rm -rf "${DEST}.new"
mv "$tmp/src" "${DEST}.new"
rm -rf "$DEST"
mv "${DEST}.new" "$DEST"
chmod 0755 "$DEST/bin/j1-mesh"

ln -sf "$DEST/bin/j1-mesh" /usr/local/bin/j1-mesh
echo "✓ Installed to $DEST (command: j1-mesh)"

exec "$DEST/bin/j1-mesh" install "$@"
