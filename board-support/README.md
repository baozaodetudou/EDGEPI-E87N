# Debian-native E87N display support

This directory is installed by `userpatches/customize-image.sh` into the new
Debian root filesystem. It is not an OpenWrt package or an on-device installer.

Dependencies: Debian python3, python3-pil, fonts-dejavu-core and
fonts-wqy-microhei. The `e87n` package
is installed under `/usr/lib/python3/dist-packages/`; `e87nctl` invokes the
system Python with `-I` so root invocations ignore caller PYTHONPATH/user-site.

`hardware.py` provides bounded telemetry reads and narrowly validated active-low
backlight writes. `display.py` owns fb0 only. Fan PWM/cooling nodes are read-only;
the kernel thermal governor remains the sole fan controller. No OpenWrt ELF,
LuCI/UCI/procd dependency, network listener or remote-control password is shipped.

See `docs/display-fan.md` for user commands and the unvalidated hardware boundary.
The NV3007 kernel patch retains its upstream GPL license and attribution.

On the target Armbian system:

```sh
e87nctl status
e87nctl fan status
e87nctl display config
e87nctl display --help
sudo e87nctl display brightness 20
sudo e87nctl display screen overview
sudo e87nctl display theme dark
sudo e87nctl display refresh 5
sudo e87nctl display rotation on
sudo e87nctl display rotation-seconds 3
sudo e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
sudo e87nctl display screen thermal
sudo e87nctl display off
sudo e87nctl display on
```

Screens: overview, cpu, memory, thermal, fan, network, traffic, storage.
Themes: dark, aurora, light. Themes are colour skins; all pages keep the same layout.
The fan remains automatic even with the display off. Configuration is
`/etc/e87n/display.json`; service name is `e87n-display.service`. Unknown
measurements are not successful hardware tests.

The default overview is 428x142 pixels, enabled at 20% brightness and refreshed
every 2 seconds. It has two fixed `LAN 1`/`LAN 2` cards with link state and an
assigned local IPv4/IPv6 address, followed by CPU usage, RAM used, CPU
temperature and kernel fan state. The fan card shows `AUTO`, cooling level and
PWM percentage only; the screen deliberately does not display tachometer RPM.
Missing values remain `--`, and neither PWM nor cooling level proves rotation.
The kernel is the only fan writer.

CPU usage comes from consecutive aggregate `/proc/stat` counters, excluding
guest double-counting and treating idle/iowait as idle. The first sample,
unchanged counters, counter resets or invalid readings show `--`. A one-shot
`e87nctl status` therefore has `cpu_usage_percent: null`; the display daemon
retains the baseline and shows usage from its second valid sample. RAM used is
`MemTotal - MemAvailable`, not free memory or a fixed board capacity.

IP reads use only the standard library: a local read-only `SIOCGIFADDR` query
for primary IPv4 and bounded `/proc/net/if_inet6` reads for IPv6. Selection
prefers carrier-up interfaces, primary IPv4, then global IPv6, with link-local
addresses as a fallback. Tentative, duplicate-failed and deprecated IPv6
addresses are excluded. There are no DNS lookups, packets or network changes.
The existing service permits only AF_UNIX, so IPv4 also has a bounded
`/proc/net/fib_trie` fallback accepting only `/32 host LOCAL` entries. That
fallback is labelled `LOCAL IPv4`, because it cannot establish the interface;
gateway, subnet and broadcast routes are not displayed as local addresses.
In production the reader opens `/proc/<current PID>/net` directly, avoiding
the `/proc/net` and `/proc/self` symlinks while retaining no-follow checks.
An assigned address does not demonstrate Internet connectivity. If no usable
address is observed, the row reads `IP --`. Inputs over 16 KiB are rejected;
at most 32 interfaces and 8 IPv6 addresses per interface are retained.

`e87nctl status` now includes addresses (`network[].ipv4`, `network[].ipv6`,
`local_ipv4`) for the local dashboard. The separate `doctor` report and its
privacy behavior are unchanged.

`display config` prints validated saved settings, or defaults when the file is
absent, without root, board probing or writes. Invalid/unsafe configuration
still returns an error. `display refresh SECONDS` accepts integers from 2 to 60
and uses the existing root-only atomic settings/apply path. Brightness, screen
and on/off behavior are preserved; changing settings while off keeps it off.
The daemon reloads settings every iteration, so a new screen, theme or interval
takes effect after its current sleep without a service restart. Rotation is
disabled by default; `refresh_seconds` refreshes data and `rotation_seconds`
changes pages.

Offline checks and preview (Pillow and DejaVu Sans required):

```sh
umask 022
python3 -B tests/test-hardware.py
python3 -B tests/test-display.py
PYTHONPATH=board-support python3 -B -m e87n.display --preview /tmp/e87n-overview.png
```

On non-Debian hosts, tests accept `E87N_TEST_FONT_DIR`, and preview accepts
`--preview-font-dir`, pointing to existing `DejaVuSans.ttf` and
`DejaVuSans-Bold.ttf`. Preview uses deterministic example data, including
documentation-only `192.0.2.87`/`2001:db8::87` addresses, and never reads live
hardware or configuration. It verifies layout, not the physical panel or fan.
