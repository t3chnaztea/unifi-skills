---
name: unifi-connect
description: >-
  Use when connecting an agent to a UniFi gateway (UDM Pro, UDM SE, Cloud
  Gateway) for the first time, or when API calls to one are failing: empty
  response bodies, curl returning HTTP 000, 401 on a key that works elsewhere,
  "how do I get a UniFi API key", "SSH is closed on my UDM", "connect to
  UniFi", "talk to my UniFi controller". Covers minting an API key over the
  API, which endpoint families accept a key, the cookie-session fallback, the
  HTTP/2 empty-body trap, and the endpoint map. Start here: the other skills
  assume this one. Not for firewall policy (unifi-firewall), Wi-Fi and radios
  (unifi-wifi), client and port operations (unifi-clients).
compatibility: >-
  UniFi OS gateways running a self-hosted Network controller. Verified on
  UniFi OS 5.1.19 / Network 10.4.57. Endpoint paths and auth behavior are
  version-sensitive; confirm against your own controller.
---

# UniFi Connect

The first thing to know: **SSH is usually closed and you do not need it.** UniFi
OS exposes a full REST API on the same host as the web UI, and everything these
skills do goes through it. The second thing: **UniFi has three overlapping API
surfaces with different auth rules**, and picking the wrong one produces errors
that look like broken credentials when they are not.

Throughout, `<UDM_HOST>` is your gateway's LAN address. Never hardcode it into a
file you might share.

## Lane 1: API key (use this)

API keys are the modern lane. No login round-trip, no cookie jar, no CSRF token,
and they sidestep the HTTP/2 bug described below.

### Minting a key over the API

The admin UI has a key page, but you do not need it. Keys are mintable from an
authenticated session:

```bash
# Log in once to get a session
curl -sk -X POST "https://<UDM_HOST>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"<ADMIN_USER>","password":"<ADMIN_PASS>"}' \
  -c /tmp/unifi_cookies -D /tmp/unifi_headers

CSRF=$(grep -i 'x-updated-csrf-token' /tmp/unifi_headers | awk '{print $2}' | tr -d '\r')

# Mint a named key
curl -sk --http1.1 -b /tmp/unifi_cookies -H "x-csrf-token: $CSRF" \
  -H "Content-Type: application/json" \
  -X POST "https://<UDM_HOST>/proxy/users/api/v2/user/self/keys" \
  -d '{"name":"agent"}'
```

The response contains the key **once**. Store it and move on. Give the agent its
own named key rather than sharing yours: named keys are individually revocable,
and when something writes a policy you did not expect you want to know which
identity did it.

The same path answers `GET`, which lists existing keys with `id`, `name`,
`masked_api_key`, timestamps, and the key's `permissions` map. Useful for
confirming a key exists without minting another, and for auditing what is out
there:

```bash
curl -sk -H "X-API-Key: $UNIFI_API_KEY" \
  "https://<UDM_HOST>/proxy/users/api/v2/user/self/keys"
```

**Read that `permissions` map once.** A key minted by an admin account inherits
that account's rights across every UniFi application on the box, Network and
Protect and the rest, not just the one you meant to automate. There is no
"read-only Network" key by default. So: create a dedicated limited admin account
and mint the key as that account rather than as your own super-admin, and treat
the key as equivalent to the password of whoever minted it.

### Using it

```bash
export UNIFI_API_KEY="..."   # from an env file, never committed, never in a skill

curl -sk -H "X-API-Key: $UNIFI_API_KEY" \
  "https://<UDM_HOST>/proxy/network/api/s/default/stat/device"
```

Self-signed cert on the gateway is normal, hence `-k`. If that bothers you, pin
the gateway's cert rather than disabling verification.

### What a key can and cannot reach

This matrix is the single most useful thing on this page. A key that works
perfectly for twenty calls and then 401s is not a broken key:

| Surface | Base path | API key? |
|---|---|:---:|
| Network, legacy | `/proxy/network/api/s/default/...` | yes |
| Network, v2 | `/proxy/network/v2/api/site/default/...` | yes |
| Protect, integration API | `/proxy/protect/integration/v1/...` | yes |
| **Protect, legacy API** | `/proxy/protect/api/...` | **no, 401** |
| UniFi OS system | `/api/system` | yes |

The legacy Protect API (`bootstrap`, `events`, the older `cameras` endpoint) is
the only reason Lane 2 still exists. If you are not reading Protect internals,
you never need a cookie.

## Lane 2: cookie session (only for legacy Protect)

```bash
curl -sk -X POST "https://<UDM_HOST>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"<API_USER>","password":"<API_PASS>"}' \
  -c /tmp/unifi_cookies -D /tmp/unifi_headers

CSRF=$(grep -i 'x-updated-csrf-token' /tmp/unifi_headers | awk '{print $2}' | tr -d '\r')

curl -sk --http1.1 -b /tmp/unifi_cookies -H "x-csrf-token: $CSRF" \
  "https://<UDM_HOST>/proxy/protect/api/bootstrap"
```

Every non-GET also needs `-H "Content-Type: application/json"`.

## The HTTP/2 empty-body trap

Worth its own section because it burns hours and looks like an auth failure.

**With cookie-session auth, every `/proxy/*` endpoint returns an empty body over
HTTP/2, even with correct cookies and CSRF token.** `curl -w "%{http_code}"`
reports `000`. The same call with `--http1.1` returns full JSON immediately.

The tell is that `/api/auth/login` itself works fine either way, so login
succeeds, you conclude auth is working, and then every subsequent call silently
returns nothing. It affects Network and Protect proxy paths alike.

- **Cookie-session auth: always pass `--http1.1` on `/proxy/*`.** Treat it as
  mandatory, not situational.
- **API-key requests are immune.** Verified: plain HTTP/2 with `X-API-Key`
  returns full bodies. This is one more reason Lane 1 is the default.

If your client library hides the HTTP version from you and cookie auth returns
empty bodies, that is this bug. Force HTTP/1.1 or switch to a key.

## Endpoint map

### UniFi OS level, `https://<UDM_HOST>/api/`

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `auth/login` | Authenticate, returns cookies and CSRF |
| GET | `system` | OS version, storage, location |

`GET /api/users` **404s** on current UniFi OS; it was removed. There is no known
OS-level user-list endpoint. If a doc or an older skill tells you to call it,
that doc predates the change.

### Network controller, `https://<UDM_HOST>/proxy/network/api/s/default/`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `stat/sysinfo` | Controller version, uptime |
| GET | `stat/health` | Subsystem health summary |
| GET | `stat/device` | Adopted devices: APs, switches, gateway |
| GET | `stat/sta` | Currently connected clients |
| GET | `stat/alluser` | All known clients including offline |
| GET | `rest/networkconf` | Networks and VLANs |
| GET | `rest/wlanconf` | Wi-Fi SSIDs |
| GET | `rest/user` | Known clients, including fixed-IP reservations |
| GET | `rest/portforward` | Port forwarding rules |
| GET | `rest/routing` | Static routes |
| GET | `rest/alarm` | Unresolved alarms |
| GET | `stat/sitedpi?type=by_app` | DPI traffic breakdown |
| POST | `cmd/stamgr` | Client ops: block, unblock, kick, forget |
| POST | `cmd/devmgr` | Device ops: restart, provision, power-cycle, speedtest |
| POST | `cmd/evtmgr` | Alarm archiving |

`rest/firewallrule` still exists and, on a zone-based-firewall controller,
**returns an empty list**. That does not mean you have no firewall. See
`unifi-firewall`.

### Network controller v2, `https://<UDM_HOST>/proxy/network/v2/api/site/default/`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `firewall-policies` | Zone-based firewall policies |
| GET | `firewall/zone` | Firewall zones, **singular**, `zones` 404s |
| POST | `system-log/all` | System log, body `{"pageSize":100,"pageNumber":0}` |

Legacy `stat/event` **404s** on Network 10.4. Events live at the v2 system-log
endpoint now, and it is a POST with a pagination body, not a GET.

**v2 PUTs may return HTTP 201 rather than 200. That is success.** Do not retry on
201, and do not treat it as a redirect.

### Protect

Two layers, different auth, as covered above.

- Integration API, `https://<UDM_HOST>/proxy/protect/integration/v1/`: accepts
  the API key. `GET /cameras`, `GET /sensors`.
- Legacy API, `https://<UDM_HOST>/proxy/protect/api/`: cookie only.
  `GET cameras`, `GET bootstrap`, `GET events?type=motion&start=<ts>&end=<ts>`.

`bootstrap` is the useful one: full Protect config and device list, including
where cameras actually are on the network, which is frequently not where their
DHCP reservations claim.

## The helper script

`scripts/udm.py` wraps the above. Standard library only, no dependencies.

```bash
export UDM_HOST=192.0.2.1
export UNIFI_API_KEY="..."

python3 udm.py                 # command list
python3 udm.py devices         # adopted devices
python3 udm.py clients --json  # compact output for piping
```

Commands: `status`, `clients` (+ `--all` / `block` / `unblock` / `kick`),
`devices` (+ `restart` / `provision` / `power-cycle <switch-mac> <port>`),
`networks`, `wlans`, `policies`, `zones`, `portforward`, `reservations`,
`events`, `alarms`, `dpi`, `protect`, `raw <METHOD> <path> ['<json>']`.

`raw` is the escape hatch: any path on the gateway, any method, so you are never
blocked waiting for the script to grow a subcommand.

In zsh, note that `"$UDM ..."` does not word-split. Use a function:

```bash
udm() { python3 /path/to/udm.py "$@"; }
```

## Read before write, always

Every skill here assumes it, so it belongs in the foundation:

1. **GET the object first.** Most `rest/*` endpoints want the whole object back
   on PUT. Partial PUTs silently drop the fields you omitted. See
   `unifi-clients`.
2. **Verify from a fresh read, not the PUT echo.** The controller will happily
   echo back a config it did not operationally apply. Two documented cases live
   in `unifi-wifi` (radio channel, and in-wall AP port VLAN).
3. **Know your out-of-band path before touching firewall or DHCP.** You are
   configuring the device that carries your management traffic.

## Version drift

Everything here was verified on **UniFi OS 5.1.19 / Network 10.4.57** in mid-2026.
Ubiquiti moves endpoints between versions with no deprecation notice: `stat/event`
died, `/api/users` died, the whole firewall model changed, and the HTTP/2 behavior
shifted inside a point release. Treat this map as a strong prior, not gospel.

Check what you are actually running before trusting any of it:

```bash
curl -sk -H "X-API-Key: $UNIFI_API_KEY" \
  "https://<UDM_HOST>/proxy/network/api/s/default/stat/sysinfo"
```

When a documented endpoint 404s, it moved. Look for a v2 equivalent first: that
has been the direction of travel for every migration so far.
