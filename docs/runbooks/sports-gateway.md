# Runbook: sports.tunderwood.com

Published 2026-09-16 at the user's request (journal 260). The dashboard is reachable from outside the home
network only through the Hetzner gateway, behind Authelia (`one_factor`, username `trey`, a session cookie
scoped to exactly `sports.tunderwood.com`). Method and conventions follow the herdr-autopilot runbook
`~/dev/herdr-autopilot/docs/webservice-deployment.md`. No secret values appear here.

## Topology

```text
browser --HTTPS--> gateway 178.156.224.97 (Caddy + Authelia, ~/media-stack/)
                     | WireGuard wg0 10.1.0.1
                     +--> Omarchy 10.1.0.3:8180 (socat user unit sports-gateway-forward)
                                          +--> 127.0.0.1:8180 (compose app-serve, the loopback dashboard)
```

- The compose file, the release script and the running stack are unchanged: no release was made or is needed.
  A full release restarts `app-serve`; the forwarder reconnects per connection and needs nothing.
- The upstream is the **loopback** app: read-only surfaces plus the kill pair (`/kill`; `/unkill` still needs the
  dashboard token). Its `/api/parlay/placed` and `/api/parlay/correct` write rules are not designed for this
  origin, so treat remote placement writes as unsupported. The 4.6 LAN listener (8443) is not involved.
- The unit file lives only in `~/.config/systemd/user/` and verbatim below, not under `deploy/`, so that it does not
  trip the autopilot's deploy trigger (SKILL.md, deploy: any non-docs diff).

## Omarchy

| Item | Value |
|---|---|
| Unit | user unit `sports-gateway-forward.service`, enabled; linger is on for `trey` |
| Bind | `10.1.0.3:8180` only (`freebind`, so it may start before `wg-gev`); the LAN address is not bound |
| Firewall | `sudo ufw allow in on wg-gev from 10.1.0.1 to any port 8180 proto tcp` (user, 2026-09-16) |

```sh
systemctl --user status sports-gateway-forward
journalctl --user -u sports-gateway-forward -n 50
```

````ini
# User unit: publishes the loopback dashboard (127.0.0.1:8180, compose `app-serve`) on the
# WireGuard address so the Hetzner gateway can proxy sports.tunderwood.com to it behind
# Authelia (user, 2026-09-16). The compose file is unchanged; no release is involved.
# Runbook: docs/runbooks/sports-gateway.md. Install:
#   cp sports-gateway-forward.service ~/.config/systemd/user/
#   systemctl --user daemon-reload && systemctl --user enable --now sports-gateway-forward
# Binds 10.1.0.3 only, never 0.0.0.0 or the LAN address. `freebind` lets it start before
# wg-gev is up. The owner's ufw rule admits only the gateway on wg-gev:
#   sudo ufw allow in on wg-gev from 10.1.0.1 to any port 8180 proto tcp
[Unit]
Description=Sports dashboard forwarder onto WireGuard (sports.tunderwood.com)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP4-LISTEN:8180,bind=10.1.0.3,freebind,reuseaddr,fork,max-children=64 TCP4:127.0.0.1:8180
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
````

## Gateway (`media@178.156.224.97`, `~/media-stack/`)

| File | Change | Backup |
|---|---|---|
| `Caddyfile.gateway` | appended the `sports.tunderwood.com` block | `Caddyfile.gateway.before-sports` |
| `gev-auth/configuration.yml` | access rule 3 and a host-scoped session cookie | `gev-auth/configuration.yml.before-sports` |

**Hand back:** apply both diffs to the owner's Mac repository `~/dev/nas-media-stack`, otherwise the next
Mac-side deploy overwrites the gateway files.

````diff
--- Caddyfile.live	2026-09-16 11:12:45.089433657 -0500
+++ Caddyfile.candidate	2026-09-16 11:12:57.767495336 -0500
@@ -117,3 +117,27 @@
         }
     }
 }
+
+# Sports harness: loopback dashboard forwarded onto WireGuard by a socat user unit on Omarchy (port 8180).
+sports.tunderwood.com {
+    encode zstd gzip
+    header {
+        X-Content-Type-Options nosniff
+        Referrer-Policy strict-origin-when-cross-origin
+        X-Frame-Options DENY
+        Content-Security-Policy "frame-ancestors 'none'"
+    }
+    @auth path /auth /auth/*
+    handle @auth {
+        reverse_proxy gev-auth:9091
+    }
+    handle {
+        forward_auth gev-auth:9091 {
+            uri /api/authz/forward-auth
+        }
+        reverse_proxy 10.1.0.3:8180 {
+            header_up -Authorization
+            flush_interval -1
+        }
+    }
+}
````

````diff
--- authelia.live	2026-09-16 11:12:46.297439881 -0500
+++ authelia.candidate	2026-09-16 11:12:57.774873127 -0500
@@ -19,6 +19,8 @@
       policy: one_factor
     - domain: build.tunderwood.com
       policy: one_factor
+    - domain: sports.tunderwood.com
+      policy: one_factor
 session:
   name: gev_session
   same_site: lax
@@ -32,6 +34,9 @@
     - domain: build.tunderwood.com
       authelia_url: https://build.tunderwood.com/auth
       default_redirection_url: https://build.tunderwood.com/
+    - domain: sports.tunderwood.com
+      authelia_url: https://sports.tunderwood.com/auth
+      default_redirection_url: https://sports.tunderwood.com/
   redis:
     host: gev-auth-redis
     port: 6379
````

## Rollback

On the gateway, in place (single-file bind mounts; never `mv`):

```sh
cd ~/media-stack
cat Caddyfile.gateway.before-sports > Caddyfile.gateway
docker exec caddy caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
docker exec caddy caddy reload --adapter caddyfile --config /etc/caddy/Caddyfile
cat gev-auth/configuration.yml.before-sports > gev-auth/configuration.yml
docker exec media-stack-gev-auth-1 authelia validate-config --config /config/configuration.yml
docker compose up -d --wait --force-recreate gev-auth
```

On Omarchy: `systemctl --user disable --now sports-gateway-forward`, then
`sudo ufw delete allow in on wg-gev from 10.1.0.1 to any port 8180 proto tcp`. The DNS record is the owner's (Namecheap).

## Verification, 2026-09-16 11:1x CT

- DNS: the gateway resolves `sports.tunderwood.com` to `178.156.224.97` (Omarchy's resolver still held a cached miss at the time).
- Caddy candidate built from the live file and validated inside the container; installed in place; reload clean.
  Authelia `validate-config` clean; `check-policy` applies `one_factor` from rule 3 for `trey`; `gev-auth` recreated healthy.
- Certificate: Let's Encrypt, `CN=sports.tunderwood.com`, curl verify result 0.
- Unauthenticated `GET /`, `/ui/`, `/api/summary`, `/healthz`: 302 to `/auth/`; `POST /kill`: 303 to `/auth/`; the kill switch
  stayed inactive (read on loopback afterwards).
- Authenticated (Authelia first-factor API, credentials and cookie values never printed): `/` 200, `/ui/` 200, `/healthz` 200,
  `/api/summary` 200.
- Backend from the gateway over the tunnel: 200. LAN `192.168.12.127:8180`: unreachable.
- `globe.tunderwood.com` and `build.tunderwood.com`: unchanged, 302 to their own portals; caddy, gev-auth, gev-auth-redis healthy.
