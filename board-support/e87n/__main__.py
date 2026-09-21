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


def _refresh(value):
    if not re.fullmatch(r"[0-9]{1,2}", value) or not 2 <= int(value) <= 60:
        raise argparse.ArgumentTypeError("SECONDS must be an integer in 2..60")
    return int(value)


def _seconds(value):
    if not re.fullmatch(r"[0-9]{1,2}", value) or not 0 <= int(value) <= 30:
        raise argparse.ArgumentTypeError("SECONDS must be an integer in 0..30")
    return int(value)


def _parser():
    parser = argparse.ArgumentParser(prog="python3 -m e87n")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="read hardware status as JSON")
    commands.add_parser("doctor", help="read-only system readiness report; not hardware validation")
    fan = commands.add_parser("fan", help="inspect or briefly test the kernel fan controller")
    fan_commands = fan.add_subparsers(dest="fan_command", required=True)
    fan_commands.add_parser("status", help="read kernel fan status as JSON")
    test = fan_commands.add_parser("test", help="temporarily set a cooling level and restore it")
    test.add_argument("state", type=int, metavar="LEVEL")
    test.add_argument("seconds", type=_seconds, nargs="?", default=5, metavar="SECONDS")
    acceleration = commands.add_parser("acceleration", help="report CPUFreq and MTK offload readiness")
    acceleration.add_subparsers(dest="acceleration_command", required=True).add_parser(
        "status", help="read acceleration status without changing hardware")
    display = commands.add_parser("display", help="view or change E87N display settings")
    setters = display.add_subparsers(dest="display_command", required=True)
    setters.add_parser("config", help="read validated saved/default settings as JSON; no writes")
    setters.add_parser("brightness", help="save brightness in 0..100 percent").add_argument(
        "percent", type=_percent, metavar="PERCENT")
    setters.add_parser("screen", help="save the active screen").add_argument("screen", choices=_SCREENS)
    setters.add_parser("refresh", help="save refresh interval in seconds (2..60)").add_argument(
        "seconds", type=_refresh, metavar="SECONDS")
    setters.add_parser("on", help="enable display using saved brightness")
    setters.add_parser("off", help="disable display, preserving brightness and screen")
    setters.add_parser("apply", help="apply saved/default settings once (systemd ExecStartPre)")
    return parser


def _main(argv=None, *, _hardware=None):
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        from .doctor import doctor
        result = doctor()
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return result["exit_code"]
    hardware = _Hardware() if _hardware is None else _hardware
    try:
        if args.command == "status":
            result = hardware.snapshot()
        elif args.command == "acceleration":
            result = hardware.acceleration()
        elif args.command == "fan":
            result = (hardware.snapshot()["fan"] if args.fan_command == "status"
                      else hardware.fan_test(args.state, args.seconds))
        elif args.display_command == "config":
            result = hardware.load_display_config()
        else:
            command = args.display_command
            changes = None
            if command == "brightness":
                changes = {"brightness_percent": args.percent}
            elif command == "screen":
                changes = {"screen": args.screen}
            elif command == "refresh":
                changes = {"refresh_seconds": args.seconds}
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
