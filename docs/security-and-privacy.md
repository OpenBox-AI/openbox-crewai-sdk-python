# Security and Privacy

## Transport

`OPENBOX_URL` must be `https://` for non-localhost hosts. `http://` is accepted only for `localhost`, `127.0.0.1`, `[::1]`. Anything else raises `OpenBoxInsecureURLError`.

## API key validation

Per-agent keys must match `obx_live_*` or `obx_test_*`. `engine.govern(crew)` round-trips each unique key against `/api/v1/auth/validate`; failure raises `OpenBoxAuthError`. Validation runs at binding time, not on every request.

## AIP request signing

When `{PREFIX}_DID` and `{PREFIX}_PRIVATE_KEY` are configured, the SDK signs every governance API request for that agent.

Headers added:

- `X-OpenBox-Agent-DID`
- `X-OpenBox-Agent-Timestamp`
- `X-OpenBox-Agent-Nonce`
- `X-OpenBox-Body-SHA256`
- `X-OpenBox-Agent-Signature` — Ed25519 signature over `METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_SHA256\n`

Validation (at agent configuration):

- DID matches `did:aip:<uuid>`
- private key is base64 that decodes to 32 bytes
- both must be configured together; setting one without the other raises `OpenBoxConfigError`

Store private keys in your runtime secret store. Don't commit them. Don't reuse one agent's key for another.

## Per-agent identity

Distinct agents in a single crew can have distinct identities — each `OpenBoxAgent` resolves credentials from its own `env_prefix`. Useful for multi-tenant or role-segregated workloads where each agent represents a separate principal.

## Body capture

HTTP request and response bodies (text content types only) are buffered inside the SDK and merged into governance payloads when needed. They aren't stored as OTel span attributes, so generic OTel exporters won't carry them.

## File capture

Disabled by default. When enabled, common system and binary paths are skipped (`/dev/`, `/proc/`, `/sys/`, `__pycache__`, `.pyc`, `.pyo`, `.so`, `.dylib`).

## API failure policy

`governance_policy` and `on_fallback` together determine what happens when Core is unreachable or returns a degraded verdict. See [configuration.md](./configuration.md#api-failure-policy).

## Debug logging

`debug_log=True` writes per-agent trace records (evaluate payloads, response verdicts, approval polling). Useful for development and incident response. Review captured fields for sensitive data before enabling broadly.
