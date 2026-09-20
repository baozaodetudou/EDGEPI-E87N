#!/usr/bin/env python3
"""One-way UDP status beacon for the E87N diagnostic host(s)."""
import socket
import time


BEACON_HOSTS = (
    "192.168.1.2",       # historical direct-link/U-Boot host
    "192.168.1.20",      # host ending in .20 on the diagnostic /24
)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        try:
            with open("/proc/uptime", encoding="ascii") as stream:
                uptime = stream.read().split()[0]
            with open("/proc/loadavg", encoding="ascii") as stream:
                load = stream.read().split()[0]
            payload = f"E87N RAMDIAG READY ip=192.168.1.1 uptime={uptime} load={load}\n".encode()
            for host in BEACON_HOSTS:
                try:
                    sock.sendto(payload, (host, 6666))
                except OSError:
                    pass
        except OSError:
            pass
        time.sleep(3)


if __name__ == "__main__":
    main()
