---
name: bao-fly-mtls-tunnel
description: Reach the weftspun-bao OpenBao on Fly.io from a desk that has a `.bao-creds-<host>` bundle, and mint GitHub App installation tokens from its GitHub secrets engine. Trigger when a task needs an OpenBao token, a GitHub token to push/PR to V-Sekai-fire, an SSH cert for the weftspun-bao 2222 tunnel, or to read/write the `agents` KV, and `bao`/`op` report "not signed in", the listener returns `tls: certificate required`, cert login answers `invalid certificate or no client certificate supplied`, or a fly-issued SSH cert is rejected on 2222. Covers the creds-bundle layout, the mutual-TLS env, reaching the internal-only API over Tailscale or through `fly proxy`, refreshing the session token by cert login, minting a tunnel cert from the SSH signer, minting GitHub tokens via `bao read github/token`, and the traps (private CA, 1 h cert TTL, no TTY for `op signin`, the plugin User-Agent ldflag, what survives an `op`/Fly logout, don't weaken TLS).
---

# Reaching weftspun-bao (OpenBao on Fly) over mTLS

`weftspun-bao` is an OpenBao cluster on Fly.io. Its API (`https://weftspun-bao.internal:8200`)
is **internal only** — the sole public service is SSH on **2222**. The listener requires
**mutual TLS**, so a token alone is not enough; every request also presents a client cert. A
desk that has been enrolled carries everything in a `~/.bao-creds-<host>-<id>/` directory
(mode 700), outside any checkout, so deleting a repo client does not take the desk's identity
with it.

## The creds bundle

    .bao-creds-fedora-cad853/
      root-ca.pem              # chibifire.com Root CA — verify the server against THIS, never -tls-skip-verify
      client-fullchain.pem     # mTLS client cert (leaf + intermediate)
      fedora-cad853-key.pem     # mTLS client private key (0600)
      fedora-cad853-cert.pem    # leaf only; .csr is the request it came from
      session-token            # a SCOPED bao token (policies: agents-rw default github-pr ssh-bao-tunnel), 24 h
      ssh-key / ssh-key.pub    # the desk's SSH keypair for the 2222 tunnel
      ssh-key-cert.pub         # a signed SSH cert — SHORT-LIVED (~1 h); re-mint when expired
      known_hosts              # 2222 host key

Prefer the bundle's `session-token` over any root token from 1Password: it is scoped and
already the right identity (`cert-fedora-cad853`). Do not extract the root credential unless
a task genuinely needs `sys` — and shred it after.

## Talk to the API

The internal address does not resolve off the Fly network. Two routes reach it; both verify
TLS properly by pinning the cert's name, no skip.

**Tailscale** (no Fly login needed). The node joins the tailnet under a new numeric suffix on
each redeploy (`weftspun-bao-8` at the time of writing), and the older names stay listed as
offline. Pick the online one:

    tailscale status --json | jq -r '.Peer[] | select(.HostName=="weftspun-bao" and .Online) | .DNSName'

The server cert names `weftspun-bao.stonecat-ratio.ts.net` and `-1` only, not the current
suffix, so pin the name the cert does carry:

    D=~/.bao-creds-fedora-cad853
    export BAO_ADDR=https://weftspun-bao-8.stonecat-ratio.ts.net:8200            BAO_TLS_SERVER_NAME=weftspun-bao.internal            BAO_CACERT=$D/root-ca.pem            BAO_CLIENT_CERT=$D/client-fullchain.pem            BAO_CLIENT_KEY=$D/fedora-cad853-key.pem            BAO_TOKEN=$(cat $D/session-token)

**Fly** (needs a Fly login on the desk). Proxy the port and point at localhost, same pinning:

    fly proxy 8200:8200 -a weftspun-bao &          # localhost:8200 -> weftspun-bao.internal:8200
    export BAO_ADDR=https://127.0.0.1:8200         # plus the same five variables as above

Either way:

    bao token lookup            # display_name cert-fedora-cad853, policies [agents-rw default github-pr ssh-bao-tunnel]

`BAO_TLS_SERVER_NAME` is what lets `BAO_ADDR` name a host the cert does not list while the cert
stays valid for `weftspun-bao.internal` — no `/etc/hosts` edit, no `-tls-skip-verify`. The token
cannot `bao secrets list` (no `sys/mounts`); that 403 is expected, not a misconfiguration.

## Refresh the session token (cert login)

When `bao token lookup` fails, the token has expired. Log in again with the bundle's cert, and
**name no cert-auth entry**:

    bao login -method=cert -no-store -token-only > $D/session-token

With no `name=`, bao picks the desk's own entry (`auth/cert/certs/<agent>`, RFD 2195: one entry
per agent, no shared wildcard entry). `-no-store` keeps the login out of the shared
`~/.bao-token`, which a peer session on the same desk would otherwise overwrite.

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
- **`name=agents-weftspun` fails as if the cert were bad.** That shared entry was deleted
  (RFD 2195), and naming any entry that does not trust the cert answers
  `invalid certificate or no client certificate supplied`. The cert is fine and no new CSR is
  needed; drop `name=`.
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

## Mint a GitHub token (the `github/` secrets engine)

bao has a GitHub App installation-token engine at `github/` (martinbaillie's
`vault-plugin-secrets-github`, baked into the image — `7-service/openbao/Dockerfile.fdb`).
Mint with the desk token (its cert role carries the `github-pr` policy — no root needed):

    bao read -field=token github/token installation_id=160444793      # V-Sekai-fire org install
    # then: git push https://x-access-token:<ghs_...>@github.com/V-Sekai-fire/<repo>.git <branch>

Installation ids: V-Sekai-fire `160444793`, V-Sekai `162792540`. `org_name=` also works now.
The token is a `ghs_` installation token (contents+pull_requests write); it expires in ~1 h.

**The User-Agent trap.** GitHub 403s any request with no `User-Agent`. The plugin sets it to
its build-stamped `projectName`, injected by an ldflag — and the module path is
`.../vault-plugin-secrets-github/v2`, so the `-X` target is
`github.com/martinbaillie/vault-plugin-secrets-github/v2/github.projectName=...`. Build without
it (or with the `/v2` missing) and every mint 403s with an empty UA. `bao read github/info`
must show `project_name` set, not `n/a`.

Rebuilding the image changes the plugin binary's sha, so after a redeploy: re-register
(`bao write sys/plugins/catalog/secret/github sha256=<new> command=vault-plugin-secrets-github`)
and `bao write sys/plugins/reload/backend plugin=github`. The mount and its App config persist
in FoundationDB; only the catalog sha needs refreshing.

## What survives an `op` / Fly logout

- The **Fly CLI token is cached on the machine** (`~/.fly/config.yml`) and survives a web/desktop
  logout, so `fly proxy` / `fly ssh` keep working until that token is revoked.
- The **desk bundle mints without `op` or root**: cert-login → a `github-pr` token → `github/token`.
  Routine bao access and GitHub minting survive an `op` logout.
- What an `op` logout **blocks**: reading the root token, the **unseal key**, or the App PEM from
  1Password. So a redeploy/restart **seals** bao and it cannot be unsealed until `op` is back
  (`bao write sys/unseal key=@op://.../2hhc…/unseal_key`), and root-only ops (enable engines, edit
  policies) are unavailable.
- A **Fly logout** closes only the `fly proxy` route; the Tailscale route (above) does not use
  Fly credentials.

## Not in scope

Issuing the bundle itself (the CSR → `client-fullchain.pem` enrollment) is separate. For pushing
commits as the App identity without this engine (signing the JWT locally from the PEM), see
[[github-app-persona]].
