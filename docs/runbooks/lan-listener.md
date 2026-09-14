# The LAN listener (`app-serve-lan`)

Phase 4.6, addendum §6 and §10; roadmap U9. Companion to `docs/runbooks/claude-omarchy-restart.md`
and `docs/runbooks/nas-to-omarchy.md`.

## What this is

One HTTPS listener on the home network, published by Compose at `192.168.12.127:8443` and
reachable from the phone and the laptop on that network — never public, never remote, never
`0.0.0.0`. Docker publishes ports ahead of ufw, so the **address** in the compose `ports` line,
not the firewall, is what keeps the listener off the WireGuard and Docker interfaces.

It is a second container on the same image as the loopback dashboard:

| | `app-serve` | `app-serve-lan` |
|---|---|---|
| Published on | `127.0.0.1:8180` | `192.168.12.127:8443` |
| Transport | HTTP | HTTPS (your own certificate) |
| Login | none (loopback only) | the owner password, on every route but `/healthz` |
| Write routes | none | the two LAN write routes, behind the session |
| Profile | always on | `lan`, i.e. only while the three files below exist |

The loopback listener and the WebSocket recorder are untouched by all of this. Turning the LAN
listener on adds a container; it changes nothing about the existing stack.

## The three files the user creates

The loop never creates, copies, reads, prints or logs any of them. Put them in
`/srv/sports-harness/secrets/`, mode `600`, owned by the uid the containers run as (`APP_UID`
in the runtime `/srv/sports-harness/.env`). `secrets/*` is git-ignored, so they never dirty the
release script's clean-main check.

```bash
harness owner-password-hash            # prints one line; paste it into the file below
# write it to /srv/sports-harness/secrets/owner_password_hash, mode 600, owned by the app uid

openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj /CN=sports-harness \
  -addext subjectAltName=IP:192.168.12.127 \
  -keyout /srv/sports-harness/secrets/lan_tls.key \
  -out /srv/sports-harness/secrets/lan_tls.crt

openssl x509 -in /srv/sports-harness/secrets/lan_tls.crt -noout -fingerprint -sha256
```

`harness owner-password-hash` prompts for the password (`getpass`, so it is never echoed and
never reaches a shell history) and prints one `scrypt$16384$8$1$...` line; it writes no file.
The hash line is the only form of the password anything stores. The harness lives in the image,
so on the host run it through the stack -- the image entrypoint is `harness` itself:

```bash
cd /srv/sports-harness && ./sports-compose run --rm --no-deps app-serve owner-password-hash
```

Each file must be a **non-empty regular file**. A directory (which is what Docker creates for a
missing bind source), a dangling symlink or a zero-byte placeholder counts as absent, on the
host side and inside the container alike.

## Installing the certificate

The certificate is self-signed, so each client has to be told once that it trusts *this* one.

1. Run the third command above and keep the `SHA256 Fingerprint=...` line on screen.
2. Copy `lan_tls.crt` to the phone and the laptop (AirDrop, a USB stick, or a download from the
   loopback dashboard over the SSH tunnel — not from an untrusted network).
3. Install it as a trusted certificate, and **before confirming, compare the fingerprint the
   device shows against the line from step 1, character by character.**

Never install it, and never click through a certificate warning, on a network you do not
control: a warning you accept blindly is exactly the situation this fingerprint check exists to
replace. If the fingerprints differ, stop and regenerate the pair.

## Turning it on

The files' presence is the switch. There is no flag to flip and no env var to edit:

1. Place the three files.
2. Run the next full release (`make deploy-omarchy`, controller-run, in an R4 window). The
   release script's `lan_active()` sees three non-empty regular files, writes
   `COMPOSE_PROFILES=lan` into the runtime `.env`, includes `app-serve-lan` in the services it
   builds, starts, waits for and records in the receipt, and validates its paper posture like
   every other app's.
3. Open `https://192.168.12.127:8443/ui/` and log in with the owner password.

The receipt under `/srv/sports-harness/releases/<stamp>/receipt.json` lists `app-serve-lan` in
`services` when it was part of that release. `/healthz` is the one route the login exempts, so
the container's own healthcheck works without a session.

## Turning it off

Remove (or empty) any one of the three files. The next full release stops including
`app-serve-lan` and removes the `COMPOSE_PROFILES` line, so it is not started again — but a
container that is already running is not stopped by that release, and a running container keeps
the files it bind-mounted even after they are deleted on the host. To take the listener down
now, on the host:

```bash
cd /srv/sports-harness && COMPOSE_PROFILES=lan ./sports-compose rm -sf app-serve-lan
```

Then confirm nothing answers on 8443 (`ss -ltn | grep 8443`) before treating it as off.

## Changing the password

The session cookie is signed with a key derived from the bytes of `owner_password_hash`, so
changing the file changes the key and every outstanding session stops verifying — there is no
session store to clear and no key file to rotate.

1. `harness owner-password-hash` again; replace the file's contents (mode `600`).
2. Restart the container so it reads the new line:
   `cd /srv/sports-harness && COMPOSE_PROFILES=lan ./sports-compose restart app-serve-lan`.
3. Log in again on each device. Devices that were logged in are now logged out.

A missing, empty, unreadable or malformed hash file makes **every** login fail. That is the
intended failure direction: no hash, no access — never "no password required".

## Rollback

```bash
git checkout <previous sha> && make deploy-omarchy-app
```

Use `make deploy-omarchy` (full) instead whenever the rollback changes `docker-compose.yml`:
that file is in the release script's `FULL_PATHS`, so an app-only release refuses it by design.
A rolled-back compose file has no `app-serve-lan` service, and the release script removes the
`COMPOSE_PROFILES` line, so the listener does not come back with the older stack; stop any
container still running from the previous release as under **Turning it off**. Rolling back
never touches the three files: they are yours, and a later release with the current compose
file switches the listener on again exactly as before.

If a release that included the listener fails, its rollback restores the previous compose file,
the previous images and the previous `COMPOSE_PROFILES` state before it reports
`failed-old-apps-restored`.

## What the loop never does

- Create, copy, read, print or log `owner_password_hash`, `lan_tls.crt` or `lan_tls.key`. It
  asks the filesystem whether they are non-empty regular files and nothing else; receipts and
  logs carry paths and sizes, never contents.
- Copy a secret to or from the host. `scripts/release-omarchy.py` has never had an `scp` in it.
- Run `ufw` or any other firewall command, or change a published address. The binding lives in
  `docker-compose.yml` and in `deploy/omarchy/host.env` (`LAN_ADDR`, `LAN_PORT`).
- Expose the listener beyond the home network, add a second listener, or touch the loopback
  `app-serve` or the recorder `app-ws`.
