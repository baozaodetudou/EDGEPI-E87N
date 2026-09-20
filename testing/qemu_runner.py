#!/usr/bin/env python3
"""Boot the final production FIT/root in ARM64 Docker using native QEMU TCG.

No mounts/chroot, substitute kernel, generated host keys, or hardware access.
The original root is immutable; only disposable copies are ever booted/resized.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import lzma
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from factory_firmware import BUILD, RELEASE, inspect_tar
from validate import HARDWARE_EXEMPTIONS, sha256_file, validate_artifact_binding, validate_report

CHECKS = ('systemd_pid1', 'dhcp', 'dns', 'timezone', 'locale', 'minimal',
          'persistence', 'first_login', 'ssh', 'apt', 'warm_reboot', 'cold_reboot',
          'host_keys', 'display_package')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def write_json(path, data):
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def setting(name, default, minimum=1, maximum=86400):
    value = int(os.environ.get(name) or default)
    require(minimum <= value <= maximum, f'{name} outside {minimum}..{maximum}')
    return value


class Runner:
    def __init__(self, args):
        self.args = args
        self.out = args.output
        self.work = None
        self.vm = None
        self.serial = None
        self.port = None
        self.known_hosts = None
        self.boot_timeout = setting('E87N_BOOT_TIMEOUT', 900)
        self.command_timeout = setting('E87N_COMMAND_TIMEOUT', 1200)
        self.grow_mib = setting('E87N_ROOT_GROW_MIB', 1024, 0, 8192)
        self.keep_disks = setting('E87N_KEEP_DISKS', 0, 0, 1)
        self.total_timeout = setting('E87N_TOTAL_TIMEOUT', 7200)
        self.nonce = uuid.uuid4().hex
        self.evidence = {'stages': {}, 'disk_copies': [], 'qemu_commands': [],
                         'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                         'execution': 'native ARM64 Docker, qemu-system-aarch64 TCG',
                         'hardware_validation': 'pending'}
        self.result = {
            'schema_version': 1, 'status': 'FAIL',
            'artifacts': {'firmware_tar': args.firmware.name, 'firmware_tar_sha256': None,
                          'fit_sha256': None, 'root_sha256': None, 'display_deb_sha256': None},
            'guest': {'kernel_release': '', 'debian_version': '', **dict.fromkeys(CHECKS, False)},
            'hardware_exemptions': [{'unit': unit, 'reason': reason}
                                    for unit, reason in HARDWARE_EXEMPTIONS.items()],
            'binding': {'source_commit': args.source_commit, 'run_id': args.run_id,
                        'run_attempt': args.run_attempt}}

    def save(self):
        write_json(self.out / 'evidence.json', self.evidence)
        write_json(self.out / 'result.json', self.result)

    def command(self, argv, log, timeout=None, data=None, check=True):
        argv = list(map(str, argv))
        with (self.out / log).open('ab') as output:
            output.write(('\n+ ' + shlex.join(argv) + '\n').encode())
            output.flush()
            # Regular output file prevents buffering large apt/serial diagnostics in RAM.
            start = output.tell()
            try:
                completed = subprocess.run(argv, input=data, stdout=output, stderr=subprocess.STDOUT,
                                           timeout=timeout or self.command_timeout)
            except subprocess.TimeoutExpired:
                output.write(b'\nTIMEOUT\n')
                raise
        with (self.out / log).open('rb') as stream:
            stream.seek(start)
            text = stream.read().decode(errors='replace')
        require(not check or completed.returncode == 0,
                f'command exited {completed.returncode}; see {log}: {shlex.join(argv)}')
        return completed.returncode, text

    def ssh_args(self, command, tty=False):
        return ['sshpass', '-e', 'ssh', '-F', '/dev/null', '-tt' if tty else '-T',
                '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
                '-o', 'KbdInteractiveAuthentication=no', '-o', 'NumberOfPasswordPrompts=1',
                '-o', 'ConnectTimeout=8', '-o', 'ConnectionAttempts=1',
                '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
                '-o', 'StrictHostKeyChecking=accept-new',
                '-o', 'UserKnownHostsFile=' + str(self.known_hosts),
                '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlias=' + self.disk_name,
                '-p', str(self.port), 'root@127.0.0.1', command]

    def ssh(self, command, log, **kwargs):
        return self.command(self.ssh_args(command), log, **kwargs)

    def preflight(self):
        require(platform.system() == 'Linux' and platform.machine() == 'aarch64',
                'runner requires native Linux ARM64; host uname is only a runner prerequisite')
        require(Path('/.dockerenv').exists(), 'run inside Docker (use testing/run-container.sh)')
        for tool in ('qemu-system-aarch64', 'sshpass', 'ssh', 'e2fsck', 'resize2fs', 'debugfs', 'blkid'):
            require(shutil.which(tool), 'missing runner dependency: ' + tool)
        require(re.fullmatch(r'[0-9a-f]{40}', self.args.source_commit), 'expected full lowercase source SHA')
        require(re.fullmatch(r'[0-9]+', self.args.run_id), 'run id must be numeric')
        require(re.fullmatch(r'[1-9][0-9]*', self.args.run_attempt), 'run attempt must be positive')
        for path in (self.args.firmware, self.args.display_deb):
            require(path.is_file() and not path.is_symlink(), 'not a regular input: ' + str(path))
        self.result['artifacts'].update(firmware_tar_sha256=sha256_file(self.args.firmware),
                                       display_deb_sha256=sha256_file(self.args.display_deb))
        self.save()
        # Use local Linux scratch, not a Docker Desktop bind mount, for TCG disk IO.
        self.work = Path(tempfile.mkdtemp(prefix='e87n-qemu-'))
        paths, control, payloads = inspect_tar(self.args.firmware, self.work)
        self.root = paths['root']
        self.fit = paths['kernel']
        self.result['artifacts'].update(fit_sha256=sha256_file(self.fit), root_sha256=sha256_file(self.root))
        self.image = self.work / 'Image'
        self.initrd = self.work / 'initrd'
        self.image.write_bytes(lzma.decompress(payloads['kernel'], format=lzma.FORMAT_ALONE, memlimit=32*1024*1024))
        self.initrd.write_bytes(payloads['ramdisk'])
        require(('Linux version ' + RELEASE + ' ').encode() in self.image.read_bytes(),
                'FIT Image does not contain the production kernel release')
        self.image_hash, self.initrd_hash = sha256_file(self.image), sha256_file(self.initrd)
        self.root_uuid = control['root_uuid']
        _, actual_uuid = self.command(['blkid', '-p', '-s', 'UUID', '-o', 'value', self.root], 'preflight.log')
        require(actual_uuid.strip() == self.root_uuid, 'CONTROL root UUID does not match ext4')
        _, listing = self.command(['debugfs', '-R', 'ls -p /etc/ssh', self.root], 'preflight.log')
        require('/sshd_config/' in listing and 'ssh_host_' not in listing,
                'unbooted final root must have sshd config and no host keys')
        self.command(['qemu-system-aarch64', '--version'], 'preflight.log')
        self.evidence.update(kernel_release_expected=RELEASE, root_uuid=self.root_uuid,
                             fit_image_sha256=self.image_hash, original_initrd_sha256=self.initrd_hash,
                             firmware_bytes=self.args.firmware.stat().st_size,
                             unbooted_root_bytes=self.root.stat().st_size,
                             build_id=control['build_id'], root_before_boot_has_no_host_keys=True)
        self.save()

    def copy_root(self, name):
        target = self.work / (name + '.ext4')
        shutil.copyfile(self.root, target)
        require(sha256_file(target) == self.result['artifacts']['root_sha256'], 'private root copy SHA mismatch')
        before = target.stat().st_size
        with target.open('rb') as stream:
            stream.seek(1024)
            sb = stream.read(1024)
        free_blocks = int.from_bytes(sb[12:16], 'little')
        if int.from_bytes(sb[96:100], 'little') & 0x80:
            free_blocks |= int.from_bytes(sb[344:348], 'little') << 32
        free_bytes = free_blocks * (1024 << int.from_bytes(sb[24:28], 'little'))
        added_mib = self.grow_mib if free_bytes < 512 * 1024 * 1024 else 0
        if added_mib:
            # Only filesystem geometry/free space changes, no files or system services patched.
            self.command(['e2fsck', '-f', '-p', target], name + '-resize.log', check=False)
            rc, _ = self.command(['e2fsck', '-f', '-n', target], name + '-resize.log', check=False)
            require(rc == 0, 'private filesystem is not clean before enlargement')
            with target.open('r+b') as stream:
                stream.truncate(before + added_mib * 1024 * 1024)
            self.command(['resize2fs', target], name + '-resize.log')
            self.command(['e2fsck', '-f', '-n', target], name + '-resize.log')
        self.evidence['disk_copies'].append({'name': name, 'source_sha256': sha256_file(self.root),
                                           'before_bytes': before, 'after_bytes': target.stat().st_size,
                                           'original_free_bytes': free_bytes, 'added_mib': added_mib,
                                           'reason': 'private APT/display test working space; final TAR unchanged'})
        self.save()
        return target

    def start(self, disk, stage):
        require(self.vm is None, 'previous QEMU not stopped')
        self.disk_name = disk.stem
        self.known_hosts = self.out / (self.disk_name + '-known-hosts')
        # A per-container loopback port never exposes guest SSH on the host/LAN.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        cmdline = (f'console=ttyAMA0,115200 earlycon=pl011,0x09000000 '
                   f'root=UUID={self.root_uuid} rootfstype=ext4 rootwait rw '
                   'net.ifnames=0 panic=30 '
                   'systemd.mask=e87n-factory-mac.service '
                   'systemd.mask=e87n-factory-resize.service')
        argv = ['qemu-system-aarch64', '-machine', 'virt,gic-version=3', '-accel', 'tcg,thread=multi',
                '-cpu', 'cortex-a53', '-smp', '2', '-m', '1024', '-display', 'none',
                '-monitor', 'none', '-serial', 'stdio', '-nic', 'none',
                '-kernel', str(self.image), '-initrd', str(self.initrd), '-append', cmdline,
                '-drive', f'if=none,id=root,file={disk},format=raw,cache=writeback',
                '-device', 'virtio-blk-device,drive=root',
                '-netdev', f'user,id=net,hostfwd=tcp:127.0.0.1:{self.port}-:22',
                '-device', 'virtio-net-device,netdev=net,mac=52:54:00:87:00:01']
        self.evidence['qemu_commands'].append({'stage': stage, 'argv': argv})
        self.serial = (self.out / (stage + '-serial.log')).open('ab', buffering=0)
        self.vm = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=self.serial,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        self.evidence['stages'][stage] = {'qemu_pid': self.vm.pid}
        self.save()
        print(f'QEMU {stage}: PID {self.vm.pid}; waiting for root password SSH (timeout {self.boot_timeout}s)', flush=True)

    def wait_ssh(self, stage, previous_boot=None):
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            require(self.vm.poll() is None, f'QEMU exited {self.vm.returncode}; see serial log')
            try:
                rc, text = self.ssh('cat /proc/sys/kernel/random/boot_id', stage + '-ssh.log', timeout=20, check=False)
            except subprocess.TimeoutExpired:
                continue  # A booting/rebooting SSH daemon may stall during the handshake.
            ids = re.findall(r'(?m)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\s*$', text)
            if rc == 0 and len(ids) == 1 and ids[0].strip() != previous_boot:
                return ids[0].strip()
            time.sleep(3)
        raise RuntimeError(f'{stage}: SSH/reboot timed out after {self.boot_timeout}s; see serial and SSH logs')

    def login(self, stage):
        # Exercise an actual interactive login shell with a PTY, including /etc/profile.
        command = "bash -lic " + shlex.quote("printf '\\nE87N_LOGIN_READY\\n'; exit")
        _, text = self.command(self.ssh_args(command, tty=True), stage + '-login.log', timeout=90)
        require('E87N_LOGIN_READY' in text, 'interactive root login did not complete')
        require(not re.search(r'(?i)(current.*password:|new.*password:|retype.*password:|create.*user.*:)', text),
                'unexpected forced first-login password/user prompt')

    def guest(self, phase):
        args = [phase, RELEASE, self.args.source_commit, self.image_hash, self.initrd_hash,
                self.result['artifacts']['display_deb_sha256'], self.nonce]
        _, text = self.ssh('bash -s -- ' + shlex.join(args), phase + '-guest.log',
                           data=(REPO / 'testing/guest-acceptance.sh').read_bytes(),
                           timeout=max(self.command_timeout, 3600) if phase == 'packages' else self.command_timeout)
        rows = [line[len('E87N_RESULT='):] for line in text.splitlines() if line.startswith('E87N_RESULT=')]
        require(len(rows) == 1, 'missing/ambiguous guest result; see ' + phase + '-guest.log')
        result = json.loads(rows[0])
        self.evidence['stages'][phase] = {**self.evidence['stages'].get(phase, {}), **result}
        for name in (*CHECKS, 'kernel_release', 'debian_version'):
            if name in result:
                self.result['guest'][name] = result[name]
        self.save()
        print('Guest phase completed: ' + phase, flush=True)
        return result

    def stop(self, graceful=False):
        if self.vm is None:
            return
        try:
            if self.vm.poll() is None and graceful:
                self.ssh('sync; systemctl poweroff --no-block', 'poweroff.log', timeout=30, check=False)
                self.vm.wait(timeout=120)
            if self.vm.poll() is None:
                self.vm.terminate()
                self.vm.wait(timeout=15)
        finally:
            if self.vm.poll() is None:
                self.vm.kill()
                self.vm.wait(timeout=15)
            self.vm = None
            if self.serial:
                self.serial.close()
                self.serial = None

    def execute(self):
        self.preflight()
        primary = self.copy_root('primary')
        os.environ['SSHPASS'] = 'doumao'  # Public factory password; never log shadow/private keys.
        self.start(primary, 'first-boot')
        initial_boot = self.wait_ssh('first-boot')
        self.login('first-boot')
        baseline = self.guest('baseline')
        require(baseline['debian_version'] == BUILD['debian_point'], 'guest Debian point version differs from build policy')
        require(baseline['boot_id'] == initial_boot, 'guest rebooted unexpectedly during baseline')
        self.result['guest']['first_login'] = True
        self.ssh('umask 077; cat > /tmp/e87n-display.deb', 'transfer-display.log', data=self.args.display_deb.read_bytes())
        self.guest('packages')
        pid = self.vm.pid
        self.ssh('sync; systemctl reboot --no-block', 'warm-reboot.log', timeout=30, check=False)
        self.wait_ssh('warm', previous_boot=initial_boot)
        require(self.vm.pid == pid and self.vm.poll() is None, 'warm reboot restarted the QEMU process')
        self.evidence['stages']['warm'] = {'qemu_pid_before': pid, 'qemu_pid_after': self.vm.pid}
        self.login('warm')
        warm = self.guest('warm')
        self.stop(graceful=True)
        self.start(primary, 'cold')
        self.wait_ssh('cold', previous_boot=warm['boot_id'])
        self.login('cold')
        self.guest('cold')
        self.stop(graceful=True)
        fresh = self.copy_root('fresh')
        self.start(fresh, 'fresh')
        self.wait_ssh('fresh')
        self.login('fresh')
        second = self.guest('fresh')
        first_keys, second_keys = baseline['host_key_fingerprints'], second['host_key_fingerprints']
        require(set(first_keys) == set(second_keys) and all(first_keys[k] != second_keys[k] for k in first_keys),
                'a new root copy did not generate independent SSH host keys')
        self.stop(graceful=True)
        self.result['guest']['host_keys'] = True
        # Rehash original inputs at the end and reuse the exact CI gate validator.
        require(sha256_file(self.root) == self.result['artifacts']['root_sha256'], 'pristine root was modified')
        candidate = {**self.result, 'status': 'PASS'}
        validate_artifact_binding(candidate, self.args.firmware, fit_path=self.fit,
                                  display_deb_path=self.args.display_deb, **candidate['binding'])
        validate_report(candidate, sha256_file(self.args.firmware), sha256_file(self.args.display_deb),
                        self.args.source_commit, self.args.run_id, self.args.run_attempt)
        self.result = candidate

    def diagnostics(self):
        if self.vm and self.vm.poll() is None and self.known_hosts:
            try:
                self.ssh('systemctl --failed --no-pager; systemctl list-jobs --no-pager; '
                         'journalctl -b --no-pager -n 300; ip address; ip route; '
                         'networkctl --no-pager status; df -h; cat /proc/cmdline',
                         'failure-guest.log', timeout=30, check=False)
            except Exception:
                pass

    def cleanup(self):
        self.stop()
        if self.work:
            if self.keep_disks:
                destination = self.out / ('private-disks-' + self.nonce)
                shutil.move(str(self.work), destination)
                self.evidence['retained_private_disks'] = str(destination)
            else:
                shutil.rmtree(self.work)
                self.evidence['private_disks_removed'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('firmware', 'display-deb', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('source-commit', 'run-id', 'run-attempt'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / '.runner.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (args.output / 'result.json').exists(), 'use a new output directory; result.json already exists')
        runner = Runner(args)
        runner.save()  # FAIL until every real guest check and binding validation completes.
        def interrupt(signum, frame):
            raise RuntimeError('runner interrupted or total deadline reached: signal ' + str(signum))
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            signal.signal(sig, interrupt)
        signal.alarm(runner.total_timeout)
        try:
            runner.execute()
        except BaseException as error:
            runner.result['status'] = 'FAIL'
            runner.evidence['error'] = str(error)
            (args.output / 'failure.log').write_text(traceback.format_exc())
            print('FAIL: ' + str(error), file=sys.stderr, flush=True)
            signal.alarm(0)
            runner.diagnostics()
        finally:
            signal.alarm(0)
            try:
                runner.cleanup()
            except Exception as error:
                runner.result['status'] = 'FAIL'
                runner.evidence['cleanup_error'] = str(error)
            runner.evidence['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            runner.save()
        print(runner.result['status'] + ': ' + str(args.output / 'result.json'), flush=True)
        return 0 if runner.result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
