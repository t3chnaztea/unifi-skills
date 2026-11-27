#!/usr/bin/env python3
"""UniFi gateway API helper (UDM Pro, UDM SE, Cloud Gateway).

Adapted from dlewis7444/unifi-claude-skill (MIT), trimmed and reorganized.

Auth: X-API-Key. The key is read from UNIFI_API_KEY, or from an env file
(UNIFI_ENV_FILE, default ~/.config/unifi/env) containing UNIFI_API_KEY=...
Mint a key with POST /proxy/users/api/v2/user/self/keys; see SKILL.md.

The legacy Protect API (/proxy/protect/api/*) rejects API keys with 401. Use
the `protect` command (integration API) or the cookie login flow in SKILL.md.

Config:
  UDM_HOST        gateway address, required (e.g. 192.0.2.1)
  UNIFI_API_KEY   API key, or put it in the env file
  UNIFI_ENV_FILE  path to env file, default ~/.config/unifi/env
  UNIFI_SITE      site name, default "default"
  UNIFI_READONLY  set to 1/true/yes to refuse every write, no exceptions

Usage: python3 udm.py <command> [args] [--json] [--dry-run]
Run with no args for the command list.

Writes are classified by HTTP method, not by command name, so a mutation
cannot slip through by being added to a new command later. Anything that is
not a GET counts as a write unless its call site explicitly says otherwise
(only the v2 system-log queries do, which are POST-based reads).
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("UNIFI_ENV_FILE", Path.home() / ".config" / "unifi" / "env"))


def env_value(name: str):
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None


def host() -> str:
    h = env_value("UDM_HOST")
    if not h:
        sys.exit(f"UDM_HOST not set (env or {ENV_FILE}). Example: export UDM_HOST=192.0.2.1")
    return h


def api_key() -> str:
    k = env_value("UNIFI_API_KEY")
    if not k:
        sys.exit(f"UNIFI_API_KEY not set (env or {ENV_FILE}). See SKILL.md to mint one.")
    return k


def site() -> str:
    # read like the other settings, so UNIFI_SITE in the env file is honoured too
    return env_value("UNIFI_SITE") or "default"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def legacy() -> str:
    return f"https://{host()}/proxy/network/api/s/{site()}"


def v2() -> str:
    return f"https://{host()}/proxy/network/v2/api/site/{site()}"


def protect_base() -> str:
    return f"https://{host()}/proxy/protect/integration/v1"


def readonly() -> bool:
    return str(env_value("UNIFI_READONLY") or "").strip().lower() in ("1", "true", "yes", "on")


DRY_RUN = False
TIMEOUT = 30          # seconds per request: a hung gateway must not hang the agent


def req(method: str, url: str, body=None, write=None):
    # Fail closed: unless a call site states otherwise, anything but GET is a write.
    if write is None:
        write = method.upper() != "GET"
    if write:
        if readonly():
            sys.exit(f"refused: UNIFI_READONLY is set, {method} {url} is a write")
        if DRY_RUN:
            # Returned, not printed: main() emits it as the single output document.
            return {"dry_run": True, "sent": False, "method": method.upper(), "url": url, "body": body}
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "X-API-Key": api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, context=CTX, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} {method} {url}\n{e.read().decode()[:2000]}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"no answer from the gateway for {method} {url}: {e}")
    if not raw:
        if method.upper() == "GET":
            # a read that returns nothing is not an empty list: don't let it look like one
            sys.exit(f"empty response to GET {url}")
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(f"not JSON from {method} {url} (an HTML page?):\n{raw[:300]}")
    if isinstance(parsed, dict) and isinstance(parsed.get("meta"), dict):
        meta = parsed["meta"]
        # the legacy API can refuse a request with HTTP 2xx and rc "error" inside
        if meta.get("rc") not in (None, "ok"):
            sys.exit(f"refused by the gateway: {method} {url}: rc={meta.get('rc')} "
                     f"{meta.get('msg', '')}")
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]  # unwrap legacy envelope
    return parsed


def out(obj, compact: bool):
    print(json.dumps(obj, separators=(",", ":")) if compact else json.dumps(obj, indent=2))


COMMANDS = {}


def cmd(name, help_text):
    def deco(fn):
        COMMANDS[name] = (fn, help_text)
        return fn
    return deco


@cmd("status", "health + sysinfo summary")
def status(a):
    return {"health": req("GET", f"{legacy()}/stat/health"),
            "sysinfo": req("GET", f"{legacy()}/stat/sysinfo")}


@cmd("clients", "active clients | --all incl. offline | block/unblock/kick <mac>")
def clients(a):
    if a and a[0] in ("block", "unblock", "kick"):
        return req("POST", f"{legacy()}/cmd/stamgr", {"cmd": f"{a[0]}-sta", "mac": a[1]})
    if a and a[0] == "--all":
        return req("GET", f"{legacy()}/stat/alluser")
    return req("GET", f"{legacy()}/stat/sta")


@cmd("devices", "APs/switches/gateway | restart <mac> | provision <mac> | power-cycle <switch-mac> <port>")
def devices(a):
    if a and a[0] == "restart":
        return req("POST", f"{legacy()}/cmd/devmgr", {"cmd": "restart", "mac": a[1]})
    if a and a[0] == "provision":
        return req("POST", f"{legacy()}/cmd/devmgr", {"cmd": "force-provision", "mac": a[1]})
    if a and a[0] == "power-cycle":
        return req("POST", f"{legacy()}/cmd/devmgr",
                   {"cmd": "power-cycle", "mac": a[1], "port_idx": int(a[2])})
    return req("GET", f"{legacy()}/stat/device")


@cmd("networks", "network/VLAN configs | update <id> '<full json>'")
def networks(a):
    if a and a[0] == "update":
        return req("PUT", f"{legacy()}/rest/networkconf/{a[1]}", json.loads(a[2]))
    return req("GET", f"{legacy()}/rest/networkconf")


@cmd("wlans", "SSIDs | update <id> '<full json>'")
def wlans(a):
    if a and a[0] == "update":
        return req("PUT", f"{legacy()}/rest/wlanconf/{a[1]}", json.loads(a[2]))
    return req("GET", f"{legacy()}/rest/wlanconf")


@cmd("policies", "zone firewall policies | create '<json>' | update <id> '<full json>' | delete <id>")
def policies(a):
    if a and a[0] == "create":
        return req("POST", f"{v2()}/firewall-policies", json.loads(a[1]))
    if a and a[0] == "update":
        return req("PUT", f"{v2()}/firewall-policies/{a[1]}", json.loads(a[2]))
    if a and a[0] == "delete":
        return req("DELETE", f"{v2()}/firewall-policies/{a[1]}")
    return req("GET", f"{v2()}/firewall-policies")


@cmd("zones", "firewall zones (CRUD via raw; endpoint is singular /firewall/zone)")
def zones(a):
    return req("GET", f"{v2()}/firewall/zone")


@cmd("portforward", "port forwards | create/update/delete like policies")
def portforward(a):
    if a and a[0] == "create":
        return req("POST", f"{legacy()}/rest/portforward", json.loads(a[1]))
    if a and a[0] == "update":
        return req("PUT", f"{legacy()}/rest/portforward/{a[1]}", json.loads(a[2]))
    if a and a[0] == "delete":
        return req("DELETE", f"{legacy()}/rest/portforward/{a[1]}")
    return req("GET", f"{legacy()}/rest/portforward")


@cmd("reservations", "known clients with fixed IPs")
def reservations(a):
    return [u for u in req("GET", f"{legacy()}/rest/user") if u.get("use_fixedip")]


@cmd("events", "system log (v2; legacy stat/event 404s on Network 10.4) | --pages N")
def events(a):
    pages = int(a[1]) if a and a[0] == "--pages" else 1
    entries = []
    for n in range(pages):
        page = req("POST", f"{v2()}/system-log/all",
                   {"pageSize": 100, "pageNumber": n}, write=False)
        # req() unwraps a {"data": [...]} envelope; anything else is a shape we don't know.
        if not isinstance(page, list):
            shape = sorted(page) if isinstance(page, dict) else type(page).__name__
            sys.exit(f"events: unexpected system-log response on page {n}: {shape}")
        entries += page
    return entries


@cmd("alarms", "alerts from the v2 system log (critical, device-alert, client-alert) | --new")
def alarms(a):
    # rest/alarm answers 400 api.err.InvalidObject from Network 10 on, and stat/alarm 404s.
    # Alarms became system-log categories; each entry carries "status" (NEW, STALED, ...).
    if a and a[0] in ("archive", "archive-all"):
        sys.exit("alarms: archiving went with rest/alarm; dismiss alerts in the UI")
    entries = []
    for cat in ("critical", "device-alert", "client-alert"):
        # every page, not just the first: an open alert on page 2 must not be missed
        for n in range(ALARM_MAX_PAGES):
            page = req("POST", f"{v2()}/system-log/{cat}",
                       {"pageSize": 100, "pageNumber": n}, write=False)
            if not isinstance(page, list):
                shape = sorted(page) if isinstance(page, dict) else type(page).__name__
                sys.exit(f"alarms: unexpected system-log/{cat} response: {shape}")
            entries += page
            if len(page) < 100:
                break
        else:
            print(f"warning: system-log/{cat} has more than {ALARM_MAX_PAGES * 100} entries; "
                  "only the newest were read", file=sys.stderr)
    if a and a[0] == "--new":
        entries = [e for e in entries if e.get("status") == "NEW"]
    return sorted(entries, key=_entry_time, reverse=True)


ALARM_MAX_PAGES = 20


def _entry_time(e):
    """Newest-first sort key that never raises: the first numeric time field found."""
    for field in ("timestamp", "time", "datetime", "date"):
        try:
            return float(e.get(field))
        except (TypeError, ValueError):
            continue
    return 0.0


@cmd("dpi", "app/category traffic breakdown")
def dpi(a):
    return req("GET", f"{legacy()}/stat/sitedpi?type=by_app")


@cmd("protect", "Protect integration API: cameras | raw <path> (e.g. /sensors)")
def protect(a):
    path = a[1] if a and a[0] == "raw" else "/cameras"
    return req("GET", f"{protect_base()}{path}")


@cmd("raw", "raw <METHOD> <path> ['<json>'] - any /proxy/* or /api/* path")
def raw(a):
    body = json.loads(a[2]) if len(a) > 2 else None
    return req(a[0].upper(), f"https://{host()}{a[1]}", body)


# Options a command understands; any other argument starting with '-' is refused, so a
# mistyped safety flag ('--dryrun', '--dry_run', '-n') stops the command instead of the
# write going out for real.
KNOWN_OPTIONS = ("--json", "--dry-run", "--all", "--pages", "--new")


def main():
    global DRY_RUN
    for x in sys.argv[1:]:
        if x.startswith("-") and x not in KNOWN_OPTIONS and not _is_number(x):
            sys.exit(f"unknown option {x!r}: nothing was sent (options: {', '.join(KNOWN_OPTIONS)})")
    flags = ("--json", "--dry-run")
    args = [x for x in sys.argv[1:] if x not in flags]
    compact = "--json" in sys.argv
    DRY_RUN = "--dry-run" in sys.argv
    if not args or args[0] not in COMMANDS:
        for name, (_, h) in COMMANDS.items():
            print(f"  {name:12} {h}")
        print("\n  --dry-run     print writes instead of sending them")
        print("  --json        compact output")
        print("  UNIFI_READONLY=1 in the env refuses every write outright")
        sys.exit(0 if not args else f"unknown command: {args[0]}")
    fn, help_text = COMMANDS[args[0]]
    try:
        result = fn(args[1:])
    except IndexError:
        sys.exit(f"missing argument: {args[0]} {help_text}")
    out(result, compact)


def _is_number(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    main()
