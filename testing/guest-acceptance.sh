#!/usr/bin/env bash
# Streamed over password-authenticated SSH; never run on the container host.
set -Eeuo pipefail
exec python3 - "$@" <<'PY'
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

phase, release, source, image_hash, initrd_hash, deb_hash, nonce = sys.argv[1:]
state_dir = Path('/var/lib/e87n-qemu-acceptance')

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def run(*args, check=True, env=None):
    print('+ ' + shlex.join(args), flush=True)
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=900, env=env)
    print(result.stdout, end='', flush=True)
    require(not check or result.returncode == 0, 'command failed: ' + shlex.join(args))
    return result.stdout.strip()

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def keys():
    pubs = sorted(Path('/etc/ssh').glob('ssh_host_*_key.pub'))
    require(len(pubs) >= 2, 'first boot did not generate multiple SSH host keys')
    fingerprints = {}
    for p in pubs:
        fields = p.read_text().split()
        require(len(fields) >= 2, 'invalid SSH public key: ' + str(p))
        raw = base64.b64decode(fields[1], validate=True)
        fingerprints[p.name] = 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
    return fingerprints  # Comments/hostnames cannot masquerade as a different key.

packages = ('linux-image-current-edgepi-e87n', 'linux-dtb-current-edgepi-e87n')

def holds():
    held = run('apt-mark', 'showhold').splitlines()
    require(set(packages) <= set(held), 'kernel/DTB packages are not held')
    return {p: run('dpkg-query', '-W', '-f=${Status}\t${Version}\t${Architecture}', p)
            for p in packages}

def identity():
    require(os.geteuid() == 0, 'not root')
    require(Path('/proc/1/comm').read_text().strip() == 'systemd', 'PID 1 is not systemd')
    require(run('uname', '-r') == release, 'not the production kernel release')
    require(run('uname', '-m') == 'aarch64', 'not ARM64')
    require(run('findmnt', '-n', '-o', 'FSTYPE', '/') == 'ext4', 'root is not ext4')
    require(run('findmnt', '-n', '-o', 'SOURCE', '/') == '/dev/vda', 'root is not the virtio private disk')
    require(sha('/boot/vmlinuz-' + release) == image_hash, 'installed Image differs from FIT Image')
    require(sha('/boot/initrd.img-' + release) == initrd_hash, 'installed initrd differs from FIT initrd')
    for name in ('build-provenance.json', 'build-recipe.json'):
        record = json.loads((Path('/usr/share/e87n') / name).read_text())
        require(record['source_commit'] == source, 'source commit differs in ' + name)
    cmdline = Path('/proc/cmdline').read_text().split()
    require(sorted(x for x in cmdline if x.startswith('systemd.mask=')) == [
        'systemd.mask=e87n-factory-mac.service', 'systemd.mask=e87n-factory-resize.service'],
        'unexpected hardware exemptions')

identity()
result = {}
if phase == 'baseline':
    run('systemctl', 'is-active', 'ssh.service', 'systemd-networkd.service', 'systemd-resolved.service')
    require(run('systemctl', 'show', '-p', 'Result', '--value', 'sshd-keygen.service') == 'success',
            'system host key generation did not succeed')
    require(run('systemctl', 'show', '-p', 'ExecMainStatus', '--value', 'sshd-keygen.service') == '0',
            'host key generator failed')
    addresses = json.loads(run('ip', '-j', '-4', 'address', 'show', 'scope', 'global'))
    leased = [a['local'] for iface in addresses for a in iface['addr_info']]
    leases = [p.read_text() for p in Path('/run/systemd/netif/leases').glob('*') if p.is_file()]
    require(any('ADDRESS=' + addr + '\n' in text + '\n' and 'SERVER_ADDRESS=' in text
                for addr in leased for text in leases), 'no active networkd DHCP lease')
    run('ip', '-4', 'route', 'show')
    run('networkctl', '--no-pager', 'status')
    run('getent', 'ahostsv4', 'deb.debian.org')
    require(run('timedatectl', 'show', '-p', 'Timezone', '--value') == 'Asia/Shanghai', 'wrong timezone')
    require(sha('/etc/localtime') == sha('/usr/share/zoneinfo/Asia/Shanghai'), 'wrong localtime')
    require(run('bash', '-lc', 'locale charmap') == 'UTF-8', 'login locale is not UTF-8')
    require(run('bash', '-lc', 'printf "%s" "$LANG"') == 'zh_CN.UTF-8',
            'login LANG is not zh_CN.UTF-8')
    require(not Path('/root/.not_logged_in_yet').exists(), 'first login setup marker remains')
    defaults = Path('/etc/default/armbian-firstrun').read_text()
    require('OPENSSHD_REGENERATE_HOST_KEYS=false' in defaults.splitlines(), 'late key regeneration enabled')
    installed = run('dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Status}\n')
    forbidden = re.compile(r'^(task-.*desktop|xserver-xorg.*|gdm3|lightdm|sddm|gnome-shell|plasma-desktop|'
                           r'docker(?:[.-].*)?|containerd(?:[.-].*)?|podman(?:[.-].*)?|'
                           r'lvm2|mdadm|luci(?:[.-].*)?)$', re.I)
    require(not any(forbidden.fullmatch(line.split('\t')[0].split(':')[0])
                    for line in installed.splitlines() if line.endswith('\tinstalled')),
            'desktop/container/LVM/RAID/LuCI packages installed in minimal firmware')
    require(not Path('/etc/systemd/system/display-manager.service').exists(), 'display manager configured')
    os_release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(os_release.get('ID', '').strip('"') == 'debian' and
            os_release.get('VERSION_CODENAME', '').strip('"') == 'trixie', 'not Debian Trixie')
    state_dir.mkdir(mode=0o700, exist_ok=False)
    state = {'nonce': nonce, 'keys': keys(), 'held': holds(),
             'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    (state_dir / 'state.json').write_text(json.dumps(state))
    (state_dir / 'persistent-file').write_text(nonce + '\n')
    run('sync')
    result = {'kernel_release': release, 'debian_version': Path('/etc/debian_version').read_text().strip(),
              **dict.fromkeys(('systemd_pid1', 'dhcp', 'dns', 'timezone', 'locale', 'minimal', 'ssh'), True),
              'host_key_fingerprints': state['keys'], 'boot_id': state['boot_id']}
elif phase == 'packages':
    state = json.loads((state_dir / 'state.json').read_text())
    require(sha('/tmp/e87n-display.deb') == deb_hash, 'transferred display package SHA mismatch')
    require(run('dpkg-deb', '-f', '/tmp/e87n-display.deb', 'Package') == 'e87n-display', 'wrong display package')
    version = run('dpkg-deb', '-f', '/tmp/e87n-display.deb', 'Version')
    apt = ['apt-get', '-o', 'Acquire::Retries=2', '-o', 'Acquire::http::Timeout=45',
           '-o', 'Acquire::https::Timeout=45', '-o', 'DPkg::Lock::Timeout=180',
           '-o', 'Dpkg::Options::=--force-confold']
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    os.environ['LC_ALL'] = 'C'
    run(*apt, '-o', 'APT::Update::Error-Mode=any', 'update')
    run(*apt, '-y', '--no-install-recommends', 'install', 'hello')
    run('hello')
    for action in ('upgrade', 'dist-upgrade'):
        plan = run(*apt, '-s', action)
        require(not any(re.match(r'^(Inst|Remv) ' + re.escape(p) + r'(?=[:\s])', line)
                        for p in packages for line in plan.splitlines()), 'APT would replace held kernel/DTB')
    run(*apt, '-y', '--no-install-recommends', 'install', '/tmp/e87n-display.deb')
    require(run('dpkg-query', '-W', '-f=${Version}', 'e87n-display') == version, 'wrong installed display version')
    require(Path('/usr/bin/e87nctl').is_file(), 'display executable missing')
    conffiles = [Path('/etc/e87n/display.json'), Path('/etc/modules-load.d/e87n-display.conf')]
    registered = run('dpkg-query', '-W', '-f=${Conffiles}', 'e87n-display')
    for p in conffiles:
        require(str(p) in registered, 'conffile not registered: ' + str(p))
        with p.open('a') as f:
            f.write('\n' if p.suffix == '.json' else '\n# QEMU persistence ' + nonce + '\n')
    preserved = {str(p): sha(p) for p in conffiles}
    # Reinstall covers the same-version lifecycle without inventing a second artifact.
    run(*apt, '-y', '--reinstall', 'install', '/tmp/e87n-display.deb')
    require(all(sha(p) == h for p, h in preserved.items()), 'reinstall overwrote conffiles')
    run(*apt, '-y', 'remove', 'e87n-display')
    require(not Path('/usr/bin/e87nctl').exists(), 'remove left executable installed')
    require(run('systemctl', 'is-active', 'e87n-display.service', check=False) in ('inactive', 'failed', 'unknown'),
            'display service remained active after remove')
    require(all(sha(p) == h for p, h in preserved.items()), 'remove deleted conffiles')
    run(*apt, '-y', 'install', '/tmp/e87n-display.deb')
    require(all(sha(p) == h for p, h in preserved.items()), 'install after remove overwrote conffiles')
    run(*apt, '-y', 'remove', 'e87n-display')
    require(holds() == state['held'], 'package operations changed held packages')
    identity()
    state['conffiles'] = preserved
    (state_dir / 'state.json').write_text(json.dumps(state))
    run('sync')
    result = {'apt': True, 'display_package': True}
elif phase in ('warm', 'cold'):
    state = json.loads((state_dir / 'state.json').read_text())
    require(state['nonce'] == nonce and (state_dir / 'persistent-file').read_text() == nonce + '\n',
            'file did not survive restart')
    require(keys() == state['keys'], 'SSH host keys changed after restart')
    require(holds() == state['held'], 'held packages changed after restart')
    require(all(sha(p) == h for p, h in state['conffiles'].items()), 'conffile did not survive restart')
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(boot_id != state['boot_id'], 'guest did not reboot')
    state['boot_id'] = boot_id
    (state_dir / 'state.json').write_text(json.dumps(state))
    run('sync')
    result = {'persistence': True, phase + '_reboot': True, 'boot_id': boot_id,
              'host_key_fingerprints': state['keys']}
elif phase == 'fresh':
    require(not state_dir.exists(), 'second guest reused first guest disk')
    run('systemctl', 'is-active', 'ssh.service')
    result = {'host_key_fingerprints': keys(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
else:
    raise RuntimeError('unknown phase: ' + phase)
print('E87N_RESULT=' + json.dumps(result, sort_keys=True), flush=True)
PY
