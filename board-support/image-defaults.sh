#!/usr/bin/env bash
# Called ONLY inside Armbian's new target chroot by customize-image.sh.
set -Eeuo pipefail
[[ "${1:-}" == --target-chroot && -d /tmp/overlay/e87n-board-support ]] || exit 2
cd /tmp/overlay/e87n-board-support

install -d /etc/ssh/sshd_config.d /etc/systemd/system/sshd-keygen.service.d \
  /etc/systemd/system/ssh.service.d /etc/systemd/system/sshd@.service.d
install -m 0644 00-e87n-security.conf /etc/ssh/sshd_config.d/
install -m 0644 systemd/e87n-keygen.conf /etc/systemd/system/sshd-keygen.service.d/e87n.conf
for unit in ssh.service sshd@.service; do
  install -m 0644 systemd/e87n-ssh-keygen.conf "/etc/systemd/system/$unit.d/e87n.conf"
done
systemctl --no-reload disable ssh.socket
systemctl --no-reload mask ssh.socket
systemctl --no-reload enable ssh.service systemd-networkd.service systemd-resolved.service systemd-timesyncd.service
sed -i 's/^OPENSSHD_REGENERATE_HOST_KEYS=.*/OPENSSHD_REGENERATE_HOST_KEYS=false/' /etc/default/armbian-firstrun
grep -qx 'OPENSSHD_REGENERATE_HOST_KEYS=false' /etc/default/armbian-firstrun

# User-requested PUBLIC factory password, never the existing board password.
# Do not trace chpasswd or log /etc/shadow. No forced account setup gate.
set +x
printf 'root:doumao\n' | chpasswd
chage -d "$(date -u +%Y-%m-%d)" -M 99999 -I -1 -E -1 root
usermod --shell /bin/bash root
# Only these known image paths are cleaned, never the live device or host.
rm -f /root/.not_logged_in_yet \
  /etc/systemd/system/getty@.service.d/override.conf \
  /etc/systemd/system/serial-getty@.service.d/override.conf \
  /etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_rsa_key.pub \
  /etc/ssh/ssh_host_ecdsa_key /etc/ssh/ssh_host_ecdsa_key.pub \
  /etc/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key.pub \
  /var/lib/dbus/machine-id /var/lib/systemd/random-seed
truncate --size=0 /etc/machine-id

install -d /etc/netplan
# Replace only Armbian's generic DHCP template in the new image. No static IP,
# bridge, DHCP server, NAT or firewall policy is imposed on either port.
rm -f /etc/netplan/10-dhcp-all-interfaces.yaml
install -m 0600 network/10-e87n-dhcp.yaml /etc/netplan/
netplan generate
# Keep Armbian's build-time resolver working: the framework still uses APT
# after this hook. Its post_debootstrap_tweaks installs the resolved stub link
# only after the last package operation (lib/functions/rootfs/post-tweaks.sh).

printf 'Asia/Shanghai\n' > /etc/timezone
ln -sfn /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
dpkg-reconfigure -f noninteractive tzdata
printf 'zh_CN.UTF-8 UTF-8\n' > /etc/locale.gen
locale-gen
update-locale --reset LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh
install -m 0644 20-e87n-updates /etc/apt/apt.conf.d/
install -d /usr/share/doc/e87n
install -m 0644 docs/*.md /usr/share/doc/e87n/
