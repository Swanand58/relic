# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

Only the latest release is supported with security fixes.

## Design principles

Relic is intentionally minimal in its attack surface:

- **Static analysis only.** No LLM calls, no network access at runtime, no
  telemetry. The only I/O is reading source files and writing a local
  `.relic/` directory.
- **Symlinks are skipped.** The indexer never follows symbolic links, which
  prevents symlink-based path-traversal attacks.
- **File-size cap.** Files above the configured threshold (default 500 KB)
  are skipped to prevent denial-of-service via large generated files.
- **No shell-out.** Relic does not execute any external commands or
  sub-processes during indexing, querying, or MCP serving.
- **No external writes.** Output is limited to `.relic/` inside the project
  root and agent config files the user explicitly opts into via `--init`.

## Reporting a vulnerability

**Do not file a public GitHub issue for security vulnerabilities.**

Instead:

1. Go to
   [github.com/Swanand58/relic/security/advisories/new](https://github.com/Swanand58/relic/security/advisories/new)
   and create a **private security advisory**, or
2. Email **khondeswanand@gmail.com** with the subject line
   `[relic security]`.

Please include:

- A description of the vulnerability
- Steps to reproduce or a proof-of-concept
- The version(s) affected
- Your assessment of severity (low / medium / high / critical)

You will receive an acknowledgement within **48 hours** and a substantive
response within **7 days**. We will coordinate disclosure timing with you.

## Scope

The following are **in scope**:

- Path traversal or directory escape during indexing
- Arbitrary code execution via crafted source files
- Denial of service (e.g. unbounded memory/CPU during index or query)
- Information leakage through MCP tool responses

The following are **out of scope**:

- Vulnerabilities in upstream dependencies (report those to the dependency
  maintainers directly; we will update promptly once a fix is available)
- Social engineering or phishing
- Attacks that require physical access to the machine
