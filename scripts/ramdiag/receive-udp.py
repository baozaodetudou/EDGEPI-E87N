#!/usr/bin/env python3
"""Capture the RAM diagnostic's one-way UDP evidence stream.

The board is expected to send from 192.168.1.1 to the host at 192.168.1.2.
The receiver never sends data back and accepts packets only from the board
address, so it is safe to run on a direct point-to-point Ethernet link.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="192.168.1.2")
    parser.add_argument("--port", type=int, default=6666)
    parser.add_argument("--source", default="192.168.1.1")
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.output = Path(f"ramdiag-udp-{stamp}.log")
    args.output = args.output.absolute()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((args.bind, args.port))
        sock.settimeout(1.0)
    finally:
        os.umask(old_umask)
    deadline = time.monotonic() + args.seconds
    packets = 0
    bytes_seen = 0
    print(f"UDP_LISTEN={args.bind}:{args.port} SOURCE={args.source}", flush=True)
    print(f"UDP_LOG={args.output}", flush=True)
    try:
        with args.output.open("xb") as stream:
            while time.monotonic() < deadline:
                try:
                    payload, source = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                if source[0] != args.source:
                    continue
                packets += 1
                bytes_seen += len(payload)
                stream.write(f"[{time.time():.6f}] {source[0]}:{source[1]} {len(payload)} bytes\n".encode())
                stream.write(payload)
                if not payload.endswith(b"\n"):
                    stream.write(b"\n")
                stream.flush()
                if packets <= 20:
                    print(payload.decode("utf-8", errors="replace")[:2000], flush=True)
                if bytes_seen >= 32 * 1024 * 1024:
                    print("UDP capture size cap reached", flush=True)
                    break
    finally:
        sock.close()
    print(f"UDP_PACKETS={packets} UDP_BYTES={bytes_seen}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

