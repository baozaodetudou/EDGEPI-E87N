#!/usr/bin/env python3
"""Runner failure/IO regressions. These never constitute a guest acceptance PASS.

Run inside the runner image: python3 /opt/e87n/testing/test_runner.py
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from qemu_runner import Runner
from validate import sha256_file


class RunnerRegression(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='e87n-runner-regression-')
        self.base = Path(self.directory.name)
        self.out = self.base / 'output'
        self.out.mkdir()
        self.args = argparse.Namespace(output=self.out, firmware=self.base / 'firmware.tar',
                                       display_deb=self.base / 'display.deb', source_commit='1' * 40,
                                       run_id='1234', run_attempt='1')

    def tearDown(self):
        self.directory.cleanup()

    def test_command_timeout_kills_local_process_and_keeps_log(self):
        runner = Runner(self.args)
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.command([sys.executable, '-c',
                            'import os,time; print(os.getpid(), flush=True); time.sleep(60)'],
                           'timeout.log', timeout=1)
        text = (self.out / 'timeout.log').read_text()
        self.assertIn('TIMEOUT', text)
        pid = int(next(line for line in text.splitlines() if line.isdecimal()))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    @unittest.skipUnless(shutil.which('mkfs.ext4'), 'needs e2fsprogs (run in Docker)')
    def test_private_ext4_growth_preserves_original_and_file_contents(self):
        tree = self.base / 'tree'
        tree.mkdir()
        (tree / 'sentinel').write_text('production files are unchanged\n')
        original = self.base / 'root.ext4'
        with original.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024)
        subprocess.run(['mkfs.ext4', '-q', '-F', '-b', '4096', '-d', str(tree), str(original)], check=True)
        expected = sha256_file(original)
        with patch.dict(os.environ, {'E87N_ROOT_GROW_MIB': '8'}):
            runner = Runner(self.args)
        runner.work = Path(tempfile.mkdtemp(dir=self.base, prefix='owned-scratch-'))
        runner.root = original
        runner.result['artifacts']['root_sha256'] = expected
        grown = runner.copy_root('primary')
        self.assertEqual(grown.stat().st_size, 24 * 1024 * 1024)
        self.assertEqual(original.stat().st_size, 16 * 1024 * 1024)
        self.assertEqual(sha256_file(original), expected)
        read = subprocess.run(['debugfs', '-R', 'cat /sentinel', str(grown)],
                              capture_output=True, text=True, check=True)
        self.assertEqual(read.stdout, 'production files are unchanged\n')
        disk = runner.evidence['disk_copies'][0]
        self.assertEqual(disk['added_mib'], 8)
        self.assertLess(disk['original_free_bytes'], 512 * 1024 * 1024)
        scratch = runner.work
        runner.cleanup()
        self.assertFalse(scratch.exists())
        self.assertEqual(sha256_file(original), expected)

    @unittest.skipUnless(shutil.which('qemu-system-aarch64'), 'needs QEMU (run in Docker)')
    def test_actual_qemu_start_failure_leaves_serial_and_is_reaped(self):
        runner = Runner(self.args)
        runner.image = self.base / 'missing-Image'
        runner.initrd = self.base / 'missing-initrd'
        runner.root_uuid = '00000000-0000-0000-0000-000000000001'
        disk = self.base / 'missing-root.ext4'
        runner.start(disk, 'failed-boot')
        process = runner.vm
        try:
            process.wait(timeout=20)
            self.assertNotEqual(process.returncode, 0)
            with self.assertRaises(RuntimeError):
                runner.wait_ssh('failed-boot')
        finally:
            runner.cleanup()
        self.assertIsNone(runner.vm)
        self.assertTrue((self.out / 'failed-boot-serial.log').read_text())
        self.assertEqual(json.loads((self.out / 'result.json').read_text())['status'], 'FAIL')
        with self.assertRaises(ProcessLookupError):
            os.kill(process.pid, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
