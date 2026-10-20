# Contributing to meshnest

Thanks for helping. A few rules keep this project honest.

## The one hard rule

**Nothing in this repo may create an inbound port, firewall rule, UPnP mapping or port-forward.**
All provider enrollment and peer traffic is outbound / hole-punched by design. PRs that open
inbound listeners (other than the dashboard's loopback bind, which is enforced in code) will be closed.

The dashboard must also stay stdlib-only Python — no runtime dependencies.

## Getting started

```bash
git clone https://github.com/OneByJorah/meshnest && cd meshnest
make test   # unit tests (no network, no root)
make lint   # shellcheck (install.sh, bin/, lib/, packaging/)
make deb    # build the .deb
```

CI runs exactly these three steps, so green locally = green remotely.

## What's useful

- Parser fixes for provider output (`dashboard/collectors.py`) — please include a fixture from your real `tailscale ping` / `netbird status` / `zerotier peers` output, with addresses anonymized.
- A new provider in `lib/provider-<name>.sh`, following the outbound-only rule and the existing shell style.
- Docs fixes — especially anything that turned out to be wrong on your distro.

## Commit / PR style

- Conventional Commits (`feat:`, `fix:`, `docs:` …). Release notes are generated from them.
- One concern per PR.
- Run `make test lint deb` before pushing.

## Security

See [SECURITY.md](SECURITY.md). Do not open public issues for vulnerabilities.
