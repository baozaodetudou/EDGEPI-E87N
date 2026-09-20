#!/usr/bin/env bash
# Native Linux builder only. Executes a DISPOSABLE COPY, never the input image.
# Existing candidate + latest userspace is an integration test, NOT a new image
# build or a target-kernel/board boot. Requires network for signed Debian APT.
set -Eeuo pipefail
umask 022
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --ssh-inside ]]; then
  root=$2
  [[ $(readlink /proc/self/ns/net) != "$(readlink /proc/1/ns/net)" ]]
  ip link set lo up
  chroot "$root" ssh-keygen -A >/dev/null
  first=$(sha256sum "$root/etc/ssh/ssh_host_ed25519_key.pub" | cut -d' ' -f1)
  chroot "$root" ssh-keygen -A >/dev/null
  [[ $(sha256sum "$root/etc/ssh/ssh_host_ed25519_key.pub" | cut -d' ' -f1) == "$first" ]]
  install -d "$root/tmp/second-identity/etc/ssh"
  chroot "$root" ssh-keygen -A -f /tmp/second-identity >/dev/null
  [[ $(sha256sum "$root/tmp/second-identity/etc/ssh/ssh_host_ed25519_key.pub" | cut -d' ' -f1) != "$first" ]]
  chroot "$root" /usr/sbin/sshd -t
  chroot "$root" /usr/sbin/sshd -T > "$root/tmp/sshd-effective.txt"
  grep -qx 'permitrootlogin yes' "$root/tmp/sshd-effective.txt"
  grep -qx 'passwordauthentication yes' "$root/tmp/sshd-effective.txt"
  grep -qx 'authenticationmethods any' "$root/tmp/sshd-effective.txt"
  chroot "$root" /usr/sbin/sshd -D -e -p 22222 -o ListenAddress=127.0.0.1 \
    -o PidFile=/run/sshd-smoke.pid > "$root/tmp/sshd-smoke.log" 2>&1 &
  server=$!
  trap 'kill -TERM "$server" 2>/dev/null || true; wait "$server" || true' EXIT
  ready=no
  for _attempt in {1..50}; do
    if ss -ltn | grep -q '127.0.0.1:22222'; then ready=yes; break; fi
    kill -0 "$server"
    sleep 0.1
  done
  [[ $ready == yes ]]
  install -m 0700 "$repo/tests/ssh-askpass-default.sh" "$root/tmp/ssh-askpass"
  # Pin the host key obtained from the private server filesystem; no TOFU.
  awk '{print "[127.0.0.1]:22222", $1, $2}' "$root/etc/ssh/ssh_host_ed25519_key.pub" > "$root/tmp/known_hosts"
  result=$(setsid env SSH_ASKPASS="$root/tmp/ssh-askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
    ssh -F /dev/null -T -p 22222 -o PubkeyAuthentication=no -o PreferredAuthentications=password \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$root/tmp/known_hosts" \
    -o NumberOfPasswordPrompts=1 -o ConnectTimeout=5 root@127.0.0.1 \
    'id -u; locale charmap' </dev/null)
  [[ "$result" == $'0\nUTF-8' ]]
  printf 'PASS: real root password SSH/PAM login; UTF-8 locale; unique persistent host keys (isolated loopback).\n'
  exit 0
fi
[[ $(id -u) == 0 && $(uname -s) == Linux ]]
if [[ ${1:-} != --inside ]]; then
  [[ $# == 1 && -f $1 && ! -L $1 ]] || { echo 'Usage: sudo bash tests/smoke-minimal-userspace.sh existing.img' >&2; exit 2; }
  exec unshare --mount --pid --fork /bin/bash "$0" --inside "$(realpath "$1")"
fi
image=$2
mount --make-rprivate /
before=$(sha256sum "$image" | cut -d' ' -f1)
scratch=$(mktemp -d /var/tmp/e87n-minimal-smoke.XXXXXXXX)
chmod 0700 "$scratch"
printf 'Disposable integration root: %s\n' "$scratch"
mkdir "$scratch/ro-root" "$scratch/ro-boot" "$scratch/root"
loop=''
cleanup() {
  local rc=$? part failed=0
  trap - EXIT
  for part in run dev/pts dev proc; do
    if mountpoint -q "$scratch/root/$part"; then umount "$scratch/root/$part" || failed=1; fi
  done
  for part in boot root; do
    if mountpoint -q "$scratch/ro-$part"; then umount "$scratch/ro-$part" || failed=1; fi
  done
  if [[ -n $loop ]] && (( failed == 0 )); then losetup --detach "$loop" || failed=1; fi
  (( failed == 0 )) || rc=1
  exit "$rc"
}
trap cleanup EXIT
loop=$(losetup --find --show --read-only --partscan "$image")
[[ $loop =~ ^/dev/loop[0-9]+$ && $(blockdev --getro "$loop") == 1 ]]
mount -t ext4 -o ro,noload,nodev,nosuid,noexec "${loop}p2" "$scratch/ro-root"
mount -t ext4 -o ro,noload,nodev,nosuid,noexec "${loop}p1" "$scratch/ro-boot"
cp -a "$scratch/ro-root/." "$scratch/root/"
cp -a "$scratch/ro-boot/." "$scratch/root/boot/"
umount "$scratch/ro-root" "$scratch/ro-boot"
losetup --detach "$loop"
loop=''
mount -t proc proc "$scratch/root/proc"
mount -t tmpfs -o mode=0755,nosuid tmpfs "$scratch/root/dev"
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$scratch/root/run"
mkdir "$scratch/root/dev/pts"
mount -t devpts -o newinstance,ptmxmode=0666,mode=0620 devpts "$scratch/root/dev/pts"
ln -s pts/ptmx "$scratch/root/dev/ptmx"
for pair in null:3 zero:5 random:8 urandom:9; do
  mknod -m 0666 "$scratch/root/dev/${pair%:*}" c 1 "${pair#*:}"
done
ln -s /proc/self/fd "$scratch/root/dev/fd"
install -d "$scratch/root/run/sshd" "$scratch/root/tmp/overlay/e87n-board-support/docs" \
  "$scratch/root/tmp/overlay/e87n-package/scripts"
printf '#!/bin/sh\nexit 101\n' > "$scratch/root/usr/sbin/policy-rc.d"
chmod 0755 "$scratch/root/usr/sbin/policy-rc.d"
cp -a "$repo/board-support/." "$scratch/root/tmp/overlay/e87n-board-support/"
python3 "$repo/scripts/write-build-provenance.py" \
  --output "$scratch/root/tmp/overlay/e87n-board-support/build-provenance.json"
cp "$repo/docs/DEFAULTS.md" "$scratch/root/tmp/overlay/e87n-board-support/docs/"
cp -a "$repo/packaging" "$repo/board-support" "$scratch/root/tmp/overlay/e87n-package/"
cp "$repo/scripts/build-display-deb.sh" "$scratch/root/tmp/overlay/e87n-package/scripts/"
cp -a "$repo/firmware" "$scratch/root/tmp/overlay/e87n-firmware"
cp "$repo/userpatches/customize-image.sh" "$scratch/root/tmp/customize-image.sh"
# Remove obsolete earlier profile ONLY from this disposable integration copy.
for name in e87n-provision-seed e87n-provision-console; do
  rm -f "$scratch/root/usr/lib/systemd/system/$name.service" \
    "$scratch/root/etc/systemd/system/multi-user.target.wants/$name.service"
done
rm -f "$scratch/root/usr/lib/python3/dist-packages/e87n/provision.py"
rm -f "$scratch/root/etc/resolv.conf"
cp -L /etc/resolv.conf "$scratch/root/etc/resolv.conf"
chroot "$scratch/root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
  /bin/bash /tmp/customize-image.sh trixie current edgepi-e87n
# Armbian still performs package work after customize_image. Verify the hook
# did not prematurely replace the build-time resolver with an absent stub.
[[ -f $scratch/root/etc/resolv.conf && ! -L $scratch/root/etc/resolv.conf ]]
chroot "$scratch/root" /usr/bin/env LC_ALL=C apt-get -o APT::Update::Error-Mode=any update
chroot "$scratch/root" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends install hello
chroot "$scratch/root" /usr/bin/hello --version >/dev/null
chroot "$scratch/root" /usr/bin/env LANG=zh_CN.UTF-8 locale charmap | grep -qx UTF-8
printf 'PASS: signed apt update and apt install hello; generated Chinese UTF-8 locale.\n'
# Emulate the pinned framework's final post_debootstrap_tweaks, AFTER APT.
ln -sfn /run/systemd/resolve/stub-resolv.conf "$scratch/root/etc/resolv.conf"
python3 -B "$repo/scripts/verify-system.py" --rootfs "$scratch/root" --bootfs "$scratch/root/boot"
unshare --net /bin/bash "$0" --ssh-inside "$scratch/root"
[[ $(sha256sum "$image" | cut -d' ' -f1) == "$before" ]]
printf 'PASS: input image unchanged. Test transformed a disposable userspace copy; no target-kernel, PID1 or physical hardware boot.\n'
