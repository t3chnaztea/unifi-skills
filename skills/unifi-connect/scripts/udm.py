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


SITE = os.environ.get("UNIFI_SITE", "default")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def legacy() -> str:
    return f"https://{host()}/proxy/network/api/s/{SITE}"


def v2() -> str:
    return f"https://{host()}/proxy/network/v2/api/site/{SITE}"


def protect_base() -> str:
    return f"https://{host()}/proxy/protect/integration/v1"


def readonly() -> bool:
    return str(env_value("UNIFI_READONLY") or "").strip().lower() in ("1", "true", "yes", "on")


DRY_RUN = False


def req(method: str, url: str, body=None, write=None):
    # Fail closed: unless a call site states otherwise, anything but GET is a write.
    if write is None:
        write = method.upper() != "GET"
    if write:
        if readonly():
            sys.exit(f"refused: UNIFI_READONLY is set, {method} {url} is a write")
        if DRY_RUN:
            out({"dry_run": True, "method": method.upper(), "url": url, "body": body}, False)
            return {"dry_run": True, "sent": False}
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "X-API-Key": api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, context=CTX) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} {method} {url}\n{e.read().decode()[:2000]}")
    if not raw:
        return {}
    parsed = json.loads(raw)
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
        entries += req("POST", f"{v2()}/system-log/all",
                       {"pageSize": 100, "pageNumber": n}, write=False)
    return entries


@cmd("alarms", "unresolved alarms | archive <id> | archive-all")
def alarms(a):
    if a and a[0] == "archive-all":
        return req("POST", f"{legacy()}/cmd/evtmgr", {"cmd": "archive-all-alarms"})
    if a and a[0] == "archive":
        return req("POST", f"{legacy()}/cmd/evtmgr", {"cmd": "archive-alarm", "_id": a[1]})
    return req("GET", f"{legacy()}/rest/alarm", None)


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


def main():
    global DRY_RUN
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
    fn, _ = COMMANDS[args[0]]
    out(fn(args[1:]), compact)


if __name__ == "__main__":
    main()
