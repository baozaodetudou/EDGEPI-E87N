#!/usr/bin/env python3
"""Runtime-oriented, no-device checks for the E87N RAM diagnostic assets."""

from __future__ import annotations

import ast
import importlib.util
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts/ramdiag/assets/init-network-first"
BEACON = ROOT / "scripts/ramdiag/assets/beacon.py"


def function_source(source: str, name: str) -> str:
    """Extract one POSIX shell function without rewriting its body."""
    marker = f"{name}() {{"
    start = source.index(marker)
    body_start = start
    depth = 0
    seen_open = False
    for position in range(start, len(source)):
        char = source[position]
        if char == "{":
            depth += 1
            seen_open = True
        elif char == "}":
            depth -= 1
            if seen_open and depth == 0:
                return source[body_start:position + 1]
    raise AssertionError(f"unterminated shell function: {name}")


class RamdiagRuntimeTest(unittest.TestCase):
    def test_init_shell_syntax_and_no_external_cat_for_carrier(self):
        source = INIT.read_text()
        result = subprocess.run(["/bin/sh", "-n", str(INIT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("read_carrier() {", source)
        self.assertIn('IFS= read -r carrier_value <"$1"', source)
        self.assertNotIn('carrier=$(cat ', source)

    def test_shell_builtin_carrier_reader_handles_up_down_and_missing_files(self):
        source = INIT.read_text()
        reader = function_source(source, "read_carrier")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "up").write_text("1\n")
            (root / "down").write_text("0\n")
            probe = f"""\
{reader}
set -eu
[ "$(read_carrier {root / 'up'})" = 1 ]
[ "$(read_carrier {root / 'down'})" = 0 ]
[ "$(read_carrier {root / 'missing'})" = 0 ]
"""
            result = subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mounted_type_uses_mount_type_field(self):
        source = INIT.read_text()
        mounted = function_source(source, "mounted_type")
        self.assertIn("mount_table=${3:-/proc/mounts}", mounted)
        self.assertIn("_ mountpoint mount_type _", mounted)
        self.assertIn('[ "$mount_type" = "$filesystem" ]', mounted)

    def test_mounted_type_matches_real_proc_mounts_field_order(self):
        mounted = function_source(INIT.read_text(), "mounted_type")
        with tempfile.TemporaryDirectory(prefix="e87n-mounted-type-") as directory:
            table = Path(directory) / "mounts"
            table.write_text(
                "proc /proc proc rw,nosuid,nodev,noexec 0 0\n"
                "sysfs /sys sysfs ro,nosuid,nodev,noexec 0 0\n"
                "tmpfs /run tmpfs rw,nosuid,nodev,noexec 0 0\n",
                encoding="utf-8",
            )
            probe = (
                f"{mounted}\n"
                'mounted_type "$1" "$2" "$3"\n'
            )
            result = subprocess.run(
                ["/bin/sh", "-c", probe, "mounted-type-probe", "/proc", "proc", str(table)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            mismatch = subprocess.run(
                ["/bin/sh", "-c", probe, "mounted-type-probe", "/proc", "sysfs", str(table)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(mismatch.returncode, 0)

    def test_beacon_targets_legacy_and_dot_twenty_hosts(self):
        tree = ast.parse(BEACON.read_text(), filename=str(BEACON))
        hosts = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "BEACON_HOSTS"
                for target in node.targets
            ):
                self.assertIsInstance(node.value, ast.Tuple)
                hosts = {element.value for element in node.value.elts if isinstance(element, ast.Constant)}
                break
        self.assertIsNotNone(hosts)
        assert hosts is not None
        self.assertIn("192.168.1.2", hosts)
        self.assertIn("192.168.1.20", hosts)
        source = BEACON.read_text()
        self.assertIn("for host in BEACON_HOSTS:", source)

    def test_beacon_main_sends_one_packet_to_each_configured_host(self):
        spec = importlib.util.spec_from_file_location("e87n_ramdiag_beacon", BEACON)
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sent: list[tuple[bytes, tuple[str, int]]] = []

        class FakeSocket:
            def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
                sent.append((payload, address))

        class StopAfterOneIteration(Exception):
            pass

        def fake_open(path: str, *, encoding: str):
            values = {
                "/proc/uptime": "12.5 0.0\n",
                "/proc/loadavg": "0.01 0.02 0.03 1/10 123\n",
            }
            return StringIO(values[path])

        with mock.patch("builtins.open", side_effect=fake_open), \
                mock.patch.object(module.socket, "socket", return_value=FakeSocket()), \
                mock.patch.object(module.time, "sleep", side_effect=StopAfterOneIteration):
            with self.assertRaises(StopAfterOneIteration):
                module.main()

        self.assertEqual([address[0] for _, address in sent], list(module.BEACON_HOSTS))
        self.assertTrue(all(address[1] == 6666 for _, address in sent))


if __name__ == "__main__":
    unittest.main()
