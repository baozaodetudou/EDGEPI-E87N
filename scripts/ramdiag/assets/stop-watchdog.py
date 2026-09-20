#!/usr/bin/env python3
"""Best-effort stop for a firmware-armed Linux watchdog during RAM diagnosis."""

from __future__ import annotations

import errno
import fcntl
import os
import struct


# Linux watchdog-api.h: _IOW('W', 4, int) and WDIOS_DISABLECARD.
WDIOC_SETOPTIONS = 0x40045704
WDIOS_DISABLECARD = 0x0001


def main() -> int:
    seen = False
    for path in ("/dev/watchdog0", "/dev/watchdog"):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
                print(f"RAMDIAG WARN: open {path}: {error}")
            continue
        seen = True
        try:
            fcntl.ioctl(fd, WDIOC_SETOPTIONS, struct.pack("I", WDIOS_DISABLECARD))
            print(f"RAMDIAG WATCHDOG DISABLED: {path}")
            return 0
        except OSError as error:
            print(f"RAMDIAG WARN: disable {path}: {error}")
        finally:
            os.close(fd)
    if not seen:
        print("RAMDIAG WATCHDOG: no watchdog device present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
