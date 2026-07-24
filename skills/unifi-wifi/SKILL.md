---
name: unifi-wifi
description: >-
  Use when UniFi Wi-Fi is slow, unstable, or being tuned: "my wifi is slow but
  speedtest on the router is fast", "great signal, terrible speed", "should I
  use 80MHz or 40MHz", "channel planning", "co-channel interference", "DFS
  channels", "my APs keep picking the same channel", "audit my SSIDs", "hidden
  SSID", or diagnosing throughput that collapses under load. Covers the
  diagnostic ladder for slow Wi-Fi, channel width and DFS tradeoffs, safe radio
  writes, SSID hygiene, and the in-wall AP port trap. Assumes unifi-connect.
  Not for firewall policy between networks (unifi-firewall), wired port and
  client operations (unifi-clients).
compatibility: >-
  UniFi Network with adopted UniFi APs. Channel and DFS specifics below are US
  regulatory domain. Verified on Network 10.4.57 with AC and WiFi 6 hardware.
---

# UniFi Wi-Fi

Most "slow Wi-Fi" is not slow Wi-Fi. It is a WAN problem, a backhaul problem, a
client problem, or a bandwidth cap somebody set two years ago. The diagnostic
ladder below exists to find that out cheaply, in that order, before touching
radios. Radio changes are the most disruptive and the most often wrong.

## The diagnostic ladder

Run it top to bottom. Each rung eliminates a layer, and the failure that motivated
this skill was only visible at the last rung.

**1. Rule out the WAN.** Trigger a gateway-side speedtest, which measures the
gateway to the internet with no Wi-Fi involved:

```bash
udm raw POST /proxy/network/api/s/default/cmd/devmgr '{"cmd":"speedtest"}'
# wait, then read the RESULT from stat/device, not stat/health
udm devices --json | python3 -c '
import json,sys
for d in json.load(sys.stdin):
    s = d.get("speedtest-status")
    if s: print(d.get("name"), s.get("xput_download"), s.get("xput_upload"),
                "status_summary=", s.get("status_summary"))'
```

The result lands on the **gateway's** device object, under the hyphenated key
`speedtest-status` (not `speedtest_status`), with throughput in the nested
`xput_download` and `xput_upload` fields. `status_summary` tells you whether the
run finished; a fresh gateway that has never run one reports zeros. `stat/health`
is a different number entirely and will send you in circles.

**2. Check the AP's uplink.** An AP on a 100 Mbps link, or meshed rather than
wired, caps every client behind it:

```bash
udm devices --json | python3 -c '
import json,sys
for d in json.load(sys.stdin):
    up = d.get("uplink") or {}
    print(d.get("name"), d.get("type"), up.get("speed"), up.get("type"))'
```

**3. Check the client's own view.** Signal, negotiated rate, satisfaction:

```bash
udm clients --json | python3 -c '
import json,sys
for c in json.load(sys.stdin):
    print(c.get("hostname"), c.get("signal"), c.get("tx_rate"), c.get("satisfaction"))'
```

This rung is where the interesting case appears. A client at **-57 dBm with an
866 Mbps PHY rate** has an excellent link. If it measures 32 Mbps, the radio is
fine and something is contending. Signal is not throughput, and confusing the
two is why people replace working access points.

**4. Rule out a bandwidth cap.** Somebody, possibly you, may have set one:

```bash
udm raw GET /proxy/network/api/s/default/rest/usergroup
udm wlans --json    # check per-SSID rate limits
```

**5. Compare every AP's channel, width, and channel utilization.** The last rung,
and the one that finds co-channel contention:

```bash
udm devices --json | python3 -c '
import json,sys
for d in json.load(sys.stdin):
    for r in d.get("radio_table_stats") or []:
        print(d.get("name"), r.get("radio"), "ch", r.get("channel"),
              "bw", r.get("bw"), "cu_total", r.get("cu_total"),
              "sat", r.get("satisfaction"))'
```

Field names matter here and are not the obvious ones. Channel width is **`bw`**,
not `channel_width`. `cu_total` is **channel utilization**, the percentage of
airtime in use, and it is the closest thing to a direct measurement of the
problem this skill is about. `cu_self_tx` and `cu_self_rx` break out how much of
that is this AP's own traffic: **high `cu_total` with low `cu_self_*` means
somebody else is using your airtime**, which is either a neighbor or, more often,
your own AP two rooms away on the same channel.

`satisfaction` reports `-1` when a radio has no clients on it. That is "no data",
not "terrible".

Read `radio_table_stats`, the operational state, not `radio_table`, the config.
They disagree more often than you would like.

## The 80 MHz trap

The failure that motivated this skill, because it is a change that looks free.

Setting every 5 GHz radio to 80 MHz doubles the theoretical per-client rate, so
it reads like a pure upgrade. It is not, because **width is bought with
channels.** In the US regulatory domain, 80 MHz with DFS channels disabled leaves
exactly **two** non-overlapping blocks:

- UNII-1: channels 36 / 40 / 44 / 48
- UNII-3: channels 149 / 153 / 157 / 161

Two blocks. If you have more than two 5 GHz APs with overlapping coverage, some
of them are sharing an 80 MHz block and taking turns transmitting. Wi-Fi degrades
gracefully in signal terms and catastrophically in contention terms: everything
looks healthy right up until two clients are busy simultaneously.

The observed case: four APs, three of them in-wall units on adjacent walls, all
piled into UNII-1 after a global switch to 80 MHz. Result was roughly **32 Mbps
on a gigabit connection**, with an 866 Mbps PHY link at -57 dBm and a confirmed
1276 Mbps gateway speedtest. Neither ISP, backhaul, nor signal was the cap.

**Do not disable DFS channels when auditing a channel plan.** It is the change
that creates the pileup. DFS opens blocks 52-64 and 100-144, which is the
difference between two usable 80 MHz blocks and roughly seven.

The tradeoff is real and worth stating honestly: DFS channels require radar
detection, cost a ~50-60 second silent scan when a radio moves onto one, and some
older or cheap client devices will not associate on them at all. Weigh that
against four APs sharing one block. In a dense deployment, **40 MHz on
non-overlapping channels beats 80 MHz on shared ones**, every time.

## Changing a radio channel safely

```bash
# 1. GET the full device object
udm devices --json > /tmp/devices.json

# 2. Extract the device's complete radio_table, change ONLY `channel`
#    on the target radio, leave every other field intact

# 3. PUT the whole device object back
udm raw PUT /proxy/network/api/s/default/rest/device/<DEVICE_ID> '<full object>'
```

**PUT the full `radio_table`.** A partial write resets the fields you omitted,
including transmit power and width on the radio you were not touching.

**Moving onto a DFS channel triggers a ~50-60 second CAC (channel availability
check).** The radio goes silent for the scan and clients drop and reconnect once.
This is normal and it is not your write failing. Do it when nobody is on a call.

**Verify from live `radio_table_stats[].channel`, not the PUT echo.** The
controller will echo back a channel it has not moved to yet, or in the DFS case,
one it is still scanning for. Re-read after the CAC completes.

## SSID hygiene

```bash
udm wlans --json | python3 -c '
import json,sys
for w in json.load(sys.stdin):
    print(w.get("name"), w.get("enabled"), w.get("security"), w.get("networkconf_id"))'
```

- **Re-read `enabled` on every `wlanconf` touch.** An SSID that was deliberately
  disabled can come back: a controller update, a config restore, or somebody in
  the UI. Deliberate disables do not always stay disabled, so make checking the
  state part of the routine rather than assuming your last change held.
- **Never leave an open SSID on the trusted network.** If a device genuinely
  cannot do WPA2, and some old handhelds and IoT gear cannot, rebuild that SSID
  on the guest or IoT network where an open join reaches nothing. An open SSID
  bridged to the trusted LAN is an unauthenticated port on your network with a
  radius of about 40 metres.
- **Hidden SSIDs are not security.** They are broadcast in probe requests by every
  client configured for them. They mainly make life harder for you.
- Confirm each SSID's `networkconf_id` maps to the network you think it does.
  This is how a device ends up on the trusted VLAN despite a careful segmentation
  plan: it joined the SSID that was still pointed at the old network.

## The in-wall AP port trap

Filed here because it is AP hardware behavior, though it bites during wired
segmentation.

**AC-series in-wall APs do not enforce native-VLAN overrides on their pass-through
data ports.** Set a port override on the AP, and the controller accepts it and
echoes `native_networkconf_id` back in `port_table` as though it applied. The
operational `forward` value stays `all`, and untagged traffic from the device
behind that port keeps bridging onto the AP's own network.

Verified with two force-provisions and a `forward:'native'` variant. The device
behind the port stayed reachable on the old subnet throughout, which is the tell:
the config says isolated, the network says otherwise.

**Devices behind in-wall AP data ports cannot be VLAN-jailed.** Not a
configuration problem, so do not burn an afternoon on payload variants. Re-cable
to a real switch port, or accept it and document the exception. Once you know
this, it takes ten seconds to check whether a stubborn device is behind one.
