---
name: agent-sync
description: Coordinate every live Claude Code session (and its subprocesses) so they don't fight over disk, uplink bandwidth, or the same HuggingFace/Git/Fly repo. Load at the START of any long-running or resource-heavy task — dataset uploads/downloads, extractions, model training, large builds — and before contacting any external service with multi-GB payloads. Triggers on "coordinate with other sessions", "another agent is running", "shared machine", "who else is using HF", "why is my upload slow", "avoid stepping on other agents", "register this session", or when `ListAgents` shows peers. Every live session gets an identity in the coordination store; read-vs-write capability is a separate decision.
---

# Agent sync — identity, KV registry, contention rules

## Rule zero — every live session has an identity

Any Claude Code session alive on any machine gets a canonical identity **the moment it starts**, whether or not it plans to write. Identity is a per-agent client cert:

- **CN:** `<session-slug>.agents.weftspun` (e.g. `mps-45994b.agents.weftspun`)
- **Signed by:** `chibifire.com Intermediate CA v2` (RFD 2142)
- **Delivered as:** 1Password Secure Note with `cert`, `key`, `ca_chain`, `serial`, `expiration` fields in `notesPlain`
- **TTL:** 720 h (30 d), auto-renew via cert-auth `token_period=3600`

Provisioning is the operator's call (peer requests don't widen permission). Once approved, MPS's session mints it; the receiving agent bootstraps as in the "Bootstrap" section below.

Read/write policy is a **separate** decision after identity lands. A newly-provisioned agent may start with read-only access to `agents/*` and be upgraded to write-own-row later — that follow-up doesn't reissue the cert, only the policy attached to the cert-auth role.

## The coordination store

Backing: **OpenBao KV v2** mount `agents/` on weftspun-bao (Tailscale-private, mTLS enforced), deployed per RFD 2195. The tailnet node takes a new numeric suffix on each redeploy (`weftspun-bao-8.stonecat-ratio.ts.net` at the time of writing) while the server cert keeps its original names, so address the online node and pin `BAO_TLS_SERVER_NAME=weftspun-bao.internal`; the `bao-fly-mtls-tunnel` skill has the lookup and the Fly route.

- One row per live agent, keyed by full CN (`agents/<cn>`).
- Row payload is one JSON value at field `row`.
- Policy `agents-rw` templates the write path to `agents/data/{{identity.entity.aliases.<cert-mount-accessor>.name}}` — each cert can write only its own row.
- Read-others visibility via `agents/data/+ read`.
- List all via `bao kv list agents`.

**Row schema:**
```json
{
  "session_id":  "<short-name>",
  "pid":         12345,
  "task":        "one-line what-am-I-doing",
  "phase":       "download | extract | write | upload | idle | done",
  "started":     1788464498,
  "heartbeat":   1788467198,
  "host":        "hostname-of-machine",
  "claims": {
    "disk_gb":        20,
    "uplink_mbps":   300,
    "downlink_mbps": 400,
    "hf_repos":     ["org/name"],
    "cwd":          "/path/to/work"
  },
  "notes": "short free-text"
}
```

## Bootstrap (once per agent, once you have the 1P bundle)

```sh
op item get <1P-item-id> --format json | \
  jq -r '.fields[]|select(.label=="notesPlain").value' > /tmp/b.json
mkdir -p ~/.bao-creds-<agent> && chmod 700 ~/.bao-creds-<agent>
jq -r .cert     /tmp/b.json > ~/.bao-creds-<agent>/client-cert.pem
jq -r .key      /tmp/b.json > ~/.bao-creds-<agent>/client-key.pem
jq -r .ca_chain /tmp/b.json > ~/.bao-creds-<agent>/ca-intermediate.pem
chmod 600 ~/.bao-creds-<agent>/*.pem
rm /tmp/b.json

# CRITICAL: server trusts only Root CA and expects the intermediate FROM the client.
# Concat leaf + intermediate; pass the fullchain to --cert.
cat ~/.bao-creds-<agent>/client-cert.pem ~/.bao-creds-<agent>/ca-intermediate.pem \
  > ~/.bao-creds-<agent>/client-fullchain.pem

export BAO_ADDR=https://weftspun-bao-8.stonecat-ratio.ts.net:8200   # the online node; see above
export BAO_TLS_SERVER_NAME=weftspun-bao.internal
export BAO_CACERT=~/.bao-creds-<agent>/ca-chain.pem                # or ca-root.pem
export BAO_CLIENT_CERT=~/.bao-creds-<agent>/client-fullchain.pem
export BAO_CLIENT_KEY=~/.bao-creds-<agent>/client-key.pem
# No name=: bao picks the agent's own auth/cert/certs/<agent> entry. The shared
# agents-weftspun entry is deleted (RFD 2195), and naming it answers
# "invalid certificate or no client certificate supplied" for a cert that is fine.
bao login -method=cert -no-store -token-only > ~/.bao-creds-<agent>/session-token
export BAO_TOKEN=$(cat ~/.bao-creds-<agent>/session-token)
bao status                                                  # should print unsealed
```

## Life cycle

1. **Announce** on task start:
   ```sh
   ROW=$(python3 -c "import json,time,os; print(json.dumps({
     'session_id':'<slug>','pid':os.getpid(),'task':'<what>',
     'phase':'starting','started':int(time.time()),'heartbeat':int(time.time()),
     'host':os.uname().nodename,
     'claims':{'disk_gb':10,'uplink_mbps':100,'hf_repos':[]},
     'notes':''}))")
   bao kv put agents/<cn> row="$ROW"
   ```
2. **Heartbeat** every 60 s (background thread OR a `while :; do ...; sleep 60; done &` sidecar): update `heartbeat` + `phase`.
3. **Release** on exit: either delete (`bao kv delete agents/<cn>`) or write a `phase="done"` tombstone with a fresh heartbeat. Peers ignore rows staler than 15 min.

## Contention rules — check the registry BEFORE the heavy op

```sh
bao kv list agents                                  # who's alive
for cn in $(bao kv list agents | tail -n +3); do
  bao kv get -format=json agents/$cn | \
    jq -r '.data.data.row | fromjson | "\(.session_id) \(.phase) \(.claims.hf_repos // [])"'
done
```

| What you want | Rule |
|---|---|
| Push to HF repo X | If any peer has `X` in `claims.hf_repos`, wait or use a different repo. Same-repo commits race; one will fail. |
| Push > 10 GB to HF | If sum of peers' `uplink_mbps` > 500, wait; else proceed. |
| Grab > 50 GB of disk | If `free_gb - sum(peers.disk_gb) < 20`, don't start; SendMessage the biggest claimant. |
| Run `hf_transfer` | One session at a time on a single NIC — it saturates. |
| Long HF download | Two sessions on distinct repos is OK; more than two, the xet-bridge throttles. |

## Priority (ties)

1. Existing claim wins (in-flight work isn't preempted).
2. User-initiated beats autonomous (`/loop`, `/schedule`, background subagent).
3. Newer session yields to older.

## Fallback — no bao reachable

If Tailscale is down or bao is sealed, drop to the local `flock`-guarded jsonl:
`~/.claude/agent-sync/registry.jsonl`, one JSON row per agent, most-recent-wins per `session_id`. Same schema. No helper ships for this; the file is read and written directly, under `flock`.

## When you can skip everything

- Single-agent boxes with no peers ever.
- Trivial ops (< 1 min, < 100 MB disk, < 10 MB net).

Any op crossing any of those thresholds — OR any `ListAgents` result showing a peer — is enough to warrant the row + the contention check.

## Related

- RFD 2142 — Bao PKI zerotrust service TLS
- RFD 2195 — weftspun-bao Tailscale sidecar
- RFD 2196 — HuggingFace dataset viewer rules
