#!/usr/bin/env bash
# Build dist/meshnest_<version>_all.deb  (usage: packaging/build-deb.sh [version])
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "$ROOT/VERSION")}"
PKG="meshnest_${VERSION}_all"
STAGE="$ROOT/dist/$PKG"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/meshnest" "$STAGE/usr/bin"

for item in bin lib dashboard config systemd VERSION LICENSE README.md; do
  cp -a "$ROOT/$item" "$STAGE/opt/meshnest/"
done
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
printf '%s\n' "$VERSION" > "$STAGE/opt/meshnest/VERSION"
chmod 0755 "$STAGE/opt/meshnest/bin/meshnest"
ln -s /opt/meshnest/bin/meshnest "$STAGE/usr/bin/meshnest"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: meshnest
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: bash, curl, gpg, ca-certificates, python3, iputils-ping, iproute2
Maintainer: OneByJorah <noreply@users.noreply.github.com>
Homepage: https://github.com/OneByJorah/meshnest
Description: No-inbound-port mesh VPN stack with a live latency dashboard
 Installs and operates Cloudflare WARP (connector), Tailscale, NetBird and
 ZeroTier from their signed apt repositories, and serves a dashboard that
 ranks the meshes by measured latency. Run "sudo meshnest install" after
 installing this package.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  echo "meshnest installed. Next: sudo meshnest install"
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/$PKG.deb" >/dev/null
echo "Built dist/$PKG.deb"
