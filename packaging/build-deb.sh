#!/usr/bin/env bash
# Build dist/j1-mesh-stack_<version>_all.deb  (usage: packaging/build-deb.sh [version])
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "$ROOT/VERSION")}"
PKG="j1-mesh-stack_${VERSION}_all"
STAGE="$ROOT/dist/$PKG"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/j1-mesh-stack" "$STAGE/usr/bin"

for item in bin lib dashboard config systemd VERSION LICENSE README.md; do
  cp -a "$ROOT/$item" "$STAGE/opt/j1-mesh-stack/"
done
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
printf '%s\n' "$VERSION" > "$STAGE/opt/j1-mesh-stack/VERSION"
chmod 0755 "$STAGE/opt/j1-mesh-stack/bin/j1-mesh"
ln -s /opt/j1-mesh-stack/bin/j1-mesh "$STAGE/usr/bin/j1-mesh"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: j1-mesh-stack
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: bash, curl, gpg, ca-certificates, python3, iputils-ping, iproute2
Maintainer: OneByJorah <noreply@users.noreply.github.com>
Homepage: https://github.com/OneByJorah/j1-mesh-stack
Description: No-inbound-port mesh VPN stack with a live latency dashboard
 Installs and operates Cloudflare WARP (connector), Tailscale, NetBird and
 ZeroTier from their signed apt repositories, and serves a dashboard that
 ranks the meshes by measured latency. Run "sudo j1-mesh install" after
 installing this package.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  echo "j1-mesh-stack installed. Next: sudo j1-mesh install"
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/$PKG.deb" >/dev/null
echo "Built dist/$PKG.deb"
