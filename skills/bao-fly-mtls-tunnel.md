---
name: bao-fly-mtls-tunnel
description: Reach the weftspun-bao OpenBao on Fly.io from a desk that has a `.bao-creds-<host>` bundle. Trigger when a task needs an OpenBao token, an SSH cert for the weftspun-bao 2222 tunnel, or to read/write the `agents` KV, and `bao`/`op` report "not signed in", the listener returns `tls: certificate required`, or a fly-issued SSH cert is rejected on 2222. Covers the creds-bundle layout, the mutual-TLS env, reaching the internal-only API through `fly proxy`, minting a fresh tunnel cert from the SSH signer, and the traps (private CA, 1 h cert TTL, no TTY for `op signin`, don't weaken TLS).
---

# Reaching weftspun-bao (OpenBao on Fly) over mTLS

`weftspun-bao` is an OpenBao cluster on Fly.io. Its API (`https://weftspun-bao.internal:8200`)
is **internal only** — the sole public service is SSH on **2222**. The listener requires
**mutual TLS**, so a token alone is not enough; every request also presents a client cert. A
desk that has been enrolled carries everything in a `~/contract-manifest/.bao-creds-<host>-<id>/`
directory (mode 700).

## The creds bundle

    .bao-creds-fedora-cad853/
      root-ca.pem              # chibifire.com Root CA — verify the server against THIS, never -tls-skip-verify
      client-fullchain.pem     # mTLS client cert (leaf + intermediate)
      fedora-cad853-key.pem     # mTLS client private key (0600)
      fedora-cad853-cert.pem    # leaf only; .csr is the request it came from
      session-token            # a SCOPED bao token (policies: agents-rw default ssh-bao-tunnel), ~12 h
      ssh-key / ssh-key.pub    # the desk's SSH keypair for the 2222 tunnel
      ssh-key-cert.pub         # a signed SSH cert — SHORT-LIVED (~1 h); re-mint when expired
      known_hosts              # 2222 host key

Prefer the bundle's `session-token` over any root token from 1Password: it is scoped and
already the right identity (`cert-fedora-cad853`). Do not extract the root credential unless
a task genuinely needs `sys` — and shred it after.

## Talk to the API (through a local tunnel)

The internal address does not resolve off the Fly network, so proxy the port and pin the
cert's name — this verifies TLS properly, no skip:

    D=~/contract-manifest/.bao-creds-fedora-cad853
    fly proxy 8200:8200 -a weftspun-bao &          # localhost:8200 -> weftspun-bao.internal:8200
    export BAO_ADDR=https://127.0.0.1:8200 \
           BAO_TLS_SERVER_NAME=weftspun-bao.internal \
           BAO_CACERT=$D/root-ca.pem \
           BAO_CLIENT_CERT=$D/client-fullchain.pem \
           BAO_CLIENT_KEY=$D/fedora-cad853-key.pem \
           BAO_TOKEN=$(cat $D/session-token)
    bao token lookup            # display_name cert-fedora-cad853, policies [agents-rw default ssh-bao-tunnel]

`BAO_TLS_SERVER_NAME` is what lets you point `BAO_ADDR` at `127.0.0.1` while the cert stays
valid for `weftspun-bao.internal` — no `/etc/hosts` edit, no `-tls-skip-verify`. The token
cannot `bao secrets list` (no `sys/mounts`); that 403 is expected, not a misconfiguration.

## Mint a fresh SSH tunnel cert

The bundle's `ssh-key-cert.pub` is a ~1 h cert and is usually stale. Re-sign the desk's public
key with the `ssh-bao-tunnel` policy's signer (mount `ssh`, role `bao-tunnel`):

    bao write -field=signed_key ssh/sign/bao-tunnel public_key=@$D/ssh-key.pub > $D/ssh-key-cert.pub

The cert carries principal `tunnel` and **only** `permit-port-forwarding` — it opens a tunnel,
it does not give a shell. Use it for port-forwarding to 2222:

    ssh -p 2222 -i $D/ssh-key -o CertificateFile=$D/ssh-key-cert.pub \
        -o UserKnownHostsFile=$D/known_hosts root@weftspun-bao.fly.dev -N -L <local>:<target>

## Traps that cost time here

- **mTLS is mandatory.** Without `BAO_CLIENT_CERT`/`BAO_CLIENT_KEY` the listener answers
  `remote error: tls: certificate required`. A token and CA are not enough.
- **Private CA.** The cluster's cert is signed by `chibifire.com Root CA`. Verify against
  `root-ca.pem`; `-tls-skip-verify` is both wrong and blocked by the auto-mode classifier.
- **Tunnel cert TTL ~1 h.** Re-mint (above) whenever `ssh-keygen -L -f ssh-key-cert.pub` shows
  it expired.
- **A Fly-issued SSH cert (`fly ssh issue`) does not open 2222.** That sshd sets
  `AuthorizedKeysFile none` and `TrustedUserCAKeys /etc/ssh/bao_ssh_ca.pub`, so only certs from
  the bao SSH CA are accepted. `fly ssh console` (WireGuard) still works for a shell.
- **`op signin` needs a TTY**, which the Claude prompt's `!` prefix does not provide
  (`inappropriate ioctl for device`). Use `OP_SERVICE_ACCOUNT_TOKEN`, or read the bundle files
  directly — they are already on disk.
- **Env does not cross the shell boundary.** A `! export …` in the prompt does not reach the
  agent's own Bash tool; hand credentials over as files (the bundle) or a service-account token.

## Not in scope

Issuing the bundle itself (the CSR → `client-fullchain.pem` enrollment) and the GitHub App
persona for pushing commits are separate; see [[github-app-persona]] for the git side.
