# Security

This is a short threat model, not a certification. Report anything you find through GitHub
security advisories on the repository.

## What is being protected

1. The Macs and devices in the fleet. Whoever can make an agent run something can drive real
   hardware that is signed in to real accounts.
2. The control plane's API keys and agent credentials.
3. Artifacts: screenshots and logs can contain whatever was on a device's screen.

## Trust boundaries

```
dashboard / API clients  --(HTTPS, bearer key, RBAC)-->  control plane  <--(outbound wss, credential)--  host agent
```

## Controls

| Threat | Control |
| --- | --- |
| A template author gains a shell on every host | Steps are a closed, typed allow-list; the agent executes by exhaustive `match` and has no "run command" step. Host tools run by argv from a fixed executable allow-list, never via a shell. `install_app` is confined to the agent's `--app-root`. |
| A stolen enrolment token | Tokens are random 256-bit, stored hashed, single use and expire (default 1 h). Redeeming one binds a host name and issues a separate long-lived credential. |
| A stolen API key | Keys are random 256-bit values stored as SHA-256 hashes (a fast hash is correct for high-entropy secrets). Roles are admin, operator and viewer; keys can be revoked; last use is tracked. The secret is shown once. |
| A partitioned or compromised agent reporting on work it no longer owns | Fencing tokens: every report must carry the current token for an active lease or it is rejected (`stale_fence`). |
| Cross-site WebSocket hijacking | The agent endpoint refuses any connection carrying an `Origin` header; agents are not browsers and never send one. |
| Browser attacks on the API | CORS is an explicit allow-list (no wildcard); `X-Content-Type-Options`, `Referrer-Policy`; artifact responses are forced to a small set of content types with `nosniff`. |
| Webhook forgery or replay | Payloads are signed `HMAC-SHA256(secret, timestamp + "." + body)` in `X-DeviceLab-Signature`, with a timestamp header receivers can bound. `verify()` in `adapters/alerts/webhook.py` is the reference implementation. |
| Tampering with history | `audit_log` is append-only, enforced by database triggers on UPDATE, DELETE and TRUNCATE rather than by application convention. |
| CSV injection through exports | Cells beginning with `=`, `+`, `-` or `@` are prefixed with a quote. |
| Input abuse | Pydantic validation on every request body and every protocol frame (`extra="forbid"`), bundle ids and locales are pattern-checked, artifacts are size-capped, pagination is bounded. |
| Secrets in the repository | None committed. `.env.example` only. The compose demo key is a published, clearly named non-secret; do not reuse it. |

## Known limitations

- Webhook URLs are checked for scheme but not against private address ranges. Run the control
  plane where outbound requests to your internal network are not possible, or add an egress
  policy, before letting untrusted admins create webhooks.
- Webhook secrets are stored as-is (an HMAC key must be recoverable). Protect the database.
- Artifacts are not scanned or redacted. Treat screenshots and logs from devices that hold
  personal data as sensitive, and do not point this at personal phones without thought.
- TLS is the operator's responsibility (terminate at a reverse proxy; use `wss://`). The
  compose file is for local use over plain HTTP.
- There is no rate limiting on the API; put it behind a proxy that has some.
- Physical devices are not yet validated beyond what `HARDWARE_VALIDATION.md` records.

## Hardening checklist for a real deployment

1. Terminate TLS in front of the API and the dashboard; use `wss://` for agents.
2. Replace the bootstrap admin key; create per-person and per-pipeline keys with the least role.
3. Set `DEVICELAB_CORS_ORIGINS` to the dashboard origin only.
4. Run agents as an unprivileged user on dedicated lab Macs; point `--app-root` at a directory
   only the build system can write.
5. Back up Postgres; set artifact retention to what your policy allows.
