"""Command line entry point: python3 -m e87n (no shell or environment overrides)."""

import argparse
import json
import re
import sys

from .hardware import HardwareError, _Hardware, _SCREENS


def _percent(value):
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 0 <= int(value) <= 100:
        raise argparse.ArgumentTypeError("PERCENT must be an integer in 0..100")
    return int(value)


def _parser():
    parser = argparse.ArgumentParser(prog="python3 -m e87n")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="read hardware status as JSON")
    fan = commands.add_parser("fan", help="read-only kernel fan status")
    fan.add_subparsers(dest="fan_command", required=True).add_parser("status")
    display = commands.add_parser("display", help="persist and apply E87N display settings")
    setters = display.add_subparsers(dest="display_command", required=True)
    setters.add_parser("brightness").add_argument("percent", type=_percent, metavar="PERCENT")
    setters.add_parser("screen").add_argument("screen", choices=_SCREENS)
    setters.add_parser("on")
    setters.add_parser("off")
    setters.add_parser("apply", help="apply saved/default settings once (systemd ExecStartPre)")
    return parser


def _main(argv=None, *, _hardware=None):
    args = _parser().parse_args(argv)
    hardware = _Hardware() if _hardware is None else _hardware
    try:
        if args.command == "status":
            result = hardware.snapshot()
        elif args.command == "fan":
            result = hardware.snapshot()["fan"]
        else:
            command = args.display_command
            changes = None
            if command == "brightness":
                changes = {"brightness_percent": args.percent}
            elif command == "screen":
                changes = {"screen": args.screen}
            elif command in ("on", "off"):
                changes = {"enabled": command == "on"}
            result = hardware.display(changes)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (HardwareError, OSError, RuntimeError) as error:
        print(f"e87n: {error}", file=sys.stderr)
        return 1


def main():
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
