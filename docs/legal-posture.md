# Legal posture — paper only

**As of 2026-09-07** (phases 0–2 deployed; phase 3 in planning). Not legal advice. Each claim
below names the query or file that proves it. Re-run every check and update the date whenever a
phase merges.

## Claims and their evidence

1. **No wager has been placed and no order has been transmitted to any venue.** The outbound
   HTTP client exposes one verb: `harness/feeds/http.py` defines `get` and nothing else, and
   `self._client.get` at line 48 is its only call site. The check
   `grep -rnE 'httpx\.(post|put|patch|delete)|_client\.(post|put|patch|delete)' harness/` returns
   nothing. The only `@app.post` routes are the local dashboard's `/kill` and `/unkill`
   (`harness/dashboard/app.py`); both are inbound and never reach a venue.

2. **Every simulated order is marked as simulated.** *Applies once phase 3 ships; neither table
   exists yet.* Then `select count(*) from orders where mode <> 'paper'` and
   `select count(*) from fills where simulated is not true` are both 0, on non-nullable columns,
   so no unset value can satisfy them.

3. **The deployed environment asserts paper mode positively.** `deploy/nas.env` carries
   `LIVE_TRADING=0` and `HARNESS_MODE=paper` in every revision, guarded by
   `tests/test_deploy_env.py`; a committed `0` proves the posture continuously through git
   history, where an absent flag would prove nothing. `grep -rn 'mode: *live' harness/ deploy/`
   also returns nothing.

4. **The Kalshi production account is unfunded, and the API key is to be read-scoped.** Unfunded
   as of this date; the account's balance page is the evidence, and the harness never reads it.
   Kalshi issues keys with `read, write` scope by default, so re-issuing the production key
   read-scoped and revoking the current one is a **pending user action**. Until it is done, this
   claim rests on the code path in claim 1 alone.

5. **The demo smoke uses mock funds and separate credentials.** *Applies once phase 4 ships.*
   It runs against Kalshi's demo environment with `secrets/kalshi_demo_key_id` and
   `secrets/kalshi_demo_private_key.pem`, files distinct from the production pair. Demo accounts
   hold no real money.

6. **The RFQ listener submits nothing.** *Applies once phase 5 ships.* It computes quotes and
   writes them to the database; it has no venue write path, which is claim 1 again.

7. **No VPN, VPS relocation, or misrepresentation of location was used at any time.** All traffic
   originates from the user's Louisiana residence or the NAS on the same network, and
   `grep -rniE 'proxy|socks' harness/` returns nothing.

8. **Going live is gated on a decision the user has not made.** `test ! -e secrets/legal_decision`
   holds today, and no autonomous process may ever write that file.

## Preflight (journaled every session)

```sh
test ! -e secrets/legal_decision \
  && grep -q '^LIVE_TRADING=0' deploy/nas.env \
  && grep -q '^HARNESS_MODE=paper' deploy/nas.env \
  && ! grep -rqE 'httpx\.(post|put|patch|delete)|_client\.(post|put|patch|delete)' harness/ \
  && echo "paper posture intact"
```

Phase 4 adds what this file lacks: a `venue_requests(method, path, ts)` table written by the single
Kalshi client, method and path only. Claim 1 then becomes an empty result set.
