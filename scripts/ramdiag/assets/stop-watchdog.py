#!/usr/bin/env python3
"""Best-effort stop for a firmware-armed Linux watchdog during RAM diagnosis."""

from __future__ import annotations

import errno
import fcntl
import os
import struct
import time


# Linux watchdog-api.h: _IOW('W', 4, int) and WDIOS_DISABLECARD.
WDIOC_SETOPTIONS = 0x40045704
WDIOS_DISABLECARD = 0x0001
WDIOC_KEEPALIVE = 0x80045705


def keepalive(fd: int, path: str) -> int:
    """Keep a watchdog armed when the driver does not support disabling it."""
    while True:
        try:
            # WDIOC_KEEPALIVE is an _IOR ioctl; use a writable four-byte
            # buffer so both 32-bit and 64-bit Python hosts pass a pointer.
            fcntl.ioctl(fd, WDIOC_KEEPALIVE, bytearray(4), True)
        except OSError:
            try:
                # Linux watchdog drivers also accept a write as a ping.  A
                # NUL is deliberately not the magic-close character 'V'.
                os.write(fd, b"\0")
            except OSError as error:
                print(f"RAMDIAG WATCHDOG KEEPER STOPPED: {path}: {error}")
                return 1
        time.sleep(0.5)


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
            try:
                fcntl.ioctl(fd, WDIOC_SETOPTIONS, struct.pack("I", WDIOS_DISABLECARD))
                print(f"RAMDIAG WATCHDOG DISABLED: {path}")
                return 0
            except OSError as error:
                print(f"RAMDIAG WATCHDOG DISABLE UNSUPPORTED: {path}: {error}")
                print(f"RAMDIAG WATCHDOG KEEPALIVE: {path}")
                return keepalive(fd, path)
        finally:
            os.close(fd)
    if not seen:
        # A device node can appear a fraction of a second after PID 1.  Let
        # the initrd supervisor retry instead of treating that race as a
        # successful stop while firmware may still be counting down.
        print("RAMDIAG WATCHDOG: no watchdog device present")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
