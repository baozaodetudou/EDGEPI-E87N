# Debian-native E87N display support

This directory is installed by `userpatches/customize-image.sh` into the new
Debian root filesystem. It is not an OpenWrt package or an on-device installer.

Dependencies: Debian python3, python3-pil, fonts-dejavu-core. The `e87n` package
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
sudo e87nctl display brightness 20
sudo e87nctl display screen thermal
sudo e87nctl display off
sudo e87nctl display on
```

Screens: overview, thermal, network, storage. The fan remains automatic even
with the display off. Configuration is `/etc/e87n/display.json`; service name
is `e87n-display.service`. Unknown measurements are not successful hardware tests.
