#!/usr/bin/env python3
"""One-way UDP status beacon for the isolated 192.168.1.0/24 link."""
import socket
import time


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        try:
            with open("/proc/uptime", encoding="ascii") as stream:
                uptime = stream.read().split()[0]
            with open("/proc/loadavg", encoding="ascii") as stream:
                load = stream.read().split()[0]
            payload = f"E87N RAMDIAG READY ip=192.168.1.1 uptime={uptime} load={load}\n".encode()
            sock.sendto(payload, ("192.168.1.2", 6666))
        except OSError:
            pass
        time.sleep(3)


if __name__ == "__main__":
    main()
