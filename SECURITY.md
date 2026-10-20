# Security

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) on this repository rather than a public issue.

## Design guarantees

- No inbound exposure: the installer never adds firewall rules, UPnP mappings or port-forwards.
- The dashboard binds to loopback by default, refuses wildcard binds, validates the `Host` header, sends a strict CSP, and renders every peer-supplied string as text (never HTML).
- Provider packages come from each vendor's signed apt repository (signing key stored in `/usr/share/keyrings` and referenced with `signed-by=`). The one exception is the ZeroTier fallback, which runs ZeroTier's own GPG-verifying installer when its apt repo has no build for your release.

## Things to know

- `/etc/j1-mesh/mesh.env` holds enrollment keys and is created with mode `0600`. Use one-off or short-lived keys: vendor CLIs take them as command-line arguments, which other local users can briefly see in the process list.
- The dashboard service runs as root so `zerotier-cli` can read its token and `ping` can open raw sockets; the unit is sandboxed (`ProtectSystem=strict`, `NoNewPrivileges`, restricted address families).
- `curl | sudo bash` trusts GitHub and this repo. Pin a tag with `JMS_REF=vX.Y.Z` and optionally a checksum with `JMS_SHA256`, or install the release `.deb`.
