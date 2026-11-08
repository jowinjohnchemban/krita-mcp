# Security policy

## Supported versions

Only the latest release is supported. The plugin and the server update themselves, so please check that
you are on the newest version (**AI Bridge (MCP) ▸ Updates ▸ Check now**, or `kritapilot --check-update`)
before reporting.

## Reporting a vulnerability

Please report it privately through GitHub:
[**Report a vulnerability**](https://github.com/jowinjohnchemban/kritapilot/security/advisories/new).
Don't open a public issue. Include what an attacker can do, the steps to reproduce it, and the versions.
You'll get an answer within a few days, and credit in the release notes if you want it.

## What is in scope

KritaPilot lets software control a creative application, so these matter most:

- **The bridge.** It listens on loopback only and every request needs the token in
  `~/.kritapilot/token`. Anything that reaches Krita without the token, or from another machine, is a bug.
- **The workspace.** The AI may read and write files only inside one folder (`~/KritaMCP` by default).
  Any way out of it (paths, links, archives, export targets) is a bug.
- **No code execution.** There is deliberately no tool that runs code, and skills are read as text only.
  Any way to make the plugin or the server run code is a bug.
- **Keys.** API keys stay in `~/.kritapilot/agent_keys.json`, are never shown, and are masked in logs.
  A key that leaks into a log, a message, a request to another service or a crash report is a bug.
- **Downloads.** Updates are taken only from this project's GitHub releases and checked against GitHub's
  checksums; generated media is fetched only from public addresses.

The full security model is in [docs/undo-and-safety.md](docs/undo-and-safety.md#security-model).
