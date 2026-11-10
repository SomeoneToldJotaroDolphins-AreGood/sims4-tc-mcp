# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
"Report a vulnerability" flow (Security → Advisories) on this repository. We
aim to acknowledge reports within a few days.

## Threat model

`sims4-tc-mcp` is a local developer tool. It can read and write files on the
machine it runs on, so it is intentionally powerful.

The HTTP transport (`SIMS4_MCP_TRANSPORT=streamable-http`) is the only
network-facing surface:

- Every request must send `Authorization: Bearer <token>`; comparisons are
  constant-time and the token comes from `SIMS4_MCP_AUTH_TOKEN` or the
  0600-mode token file.
- Binding a non-loopback address without a token **refuses to start**.
- The token travels in cleartext over plain HTTP. Treat LAN traffic as
  untrusted and use an SSH tunnel (`ssh -L 7348:127.0.0.1:7348 host`) when in
  doubt.
- Do not expose the port to the public internet.

The stdio transport inherits the privileges of the process that spawned it.

## Secrets

The bearer token is generated locally and stored outside the repository:

```
~/.config/sims4-tc-mcp/token        # 0600
~/.config/sims4-tc-mcp/config.json  # 0600
```

`.gitignore` excludes the usual secret file names. If a token is ever
committed or leaked, rotate it by deleting the file and re-running
`sims4-tc-mcp-setup`.
