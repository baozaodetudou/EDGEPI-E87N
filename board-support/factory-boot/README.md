# E87N factory-layout rootfs adaptation

Run `python3 scripts/prepare-factory-rootfs.py --root ROOT --boot BOOT --uuid UUID`
on an idle, dedicated writable ext4 loop mount after Armbian has generated fstab.
BOOT is a separate mounted/extracted bootfs. Unmount root/boot before adaptation.
The script never mounts, chroots, runs target programs or changes GPT. It refuses
symlink roots, physical-device roots, nested mounts, mismatched UUIDs, unheld
kernel/DTB packages and unexpected existing managed files. Identical reruns work.
Do not run concurrent writers. An interrupted operation may be retried; conflicting
files are rejected. A source bootfs change requires a fresh disposable root copy.

`--verify` and importable `check_offline(root, boot, uuid)` check boot copying,
fstab, installed helper/service bytes and modes, links, mask, holds and empty APT
lists without mutating anything. The helper is `/usr/lib/e87n/factory-boot.py`.
The offline gate does not validate the FIT, filesystem size, actual UUID or DTB;
the final artifact verifier owns those checks. CLI adaptation independently
compares blkid's UUID and inspects raw initrds with host lsinitramfs, rejecting
growroot/growpart/resize2fs. Never silently strip an initrd: rebuild and audit it.

The old /boot UUID mount is removed; source bootfs files and symlinks are copied
under rootfs /boot, excluding lost+found. APT list files alone are deleted; empty
directories retain their ownership/modes. No modules, firmware or essential
programs are pruned. The packager must shrink ext4 and enforce its <=768 MiB
uncompressed TAR policy; deleting indexes does not shrink the raw filesystem.

The factory layout uses 512-byte sectors:

| Partition | Name | Start | Sectors |
| --- | --- | ---: | ---: |
| p1 | u-boot-env | 8192 | 1024 |
| p2 | factory | 9216 | 8192 |
| p3 | fip | 17408 | 4096 |
| p4 | kernel | 21504 | 65536 |
| p5 | rootfs | 87040 | 15181791 |

Both runtime actions require native aarch64, E87N model/compatible, non-removable
MMC, exactly these labels/bounds and an ext4 live root on mmcblk0p5 with
matching device numbers. Resize additionally requires a writable root; the MAC
action accepts its ProtectSystem=strict read-only service namespace. The resize service invokes only
`/usr/sbin/resize2fs /dev/mmcblk0p5`; it never edits a partition table or reboots.
It validates the 4 KiB ext4 geometry, skips an already expanded filesystem and
checks the resulting size. Generic armbian-resize is masked and disabled and
/root/.no_rootfs_resize is installed. There is no generic device argument.

The MAC service waits up to five seconds for udev and validates both eth0/eth1
of_node paths against GMAC0/1 aliases before reading twelve bytes from p2 at
0x24 (six bytes per address). Both addresses must be distinct unicast nonzero
values. It refuses interfaces already administratively up; it sets MACs before
networkd starts and clones netplan's generated e87n-wired DHCP config into two
volatile per-alias policies. It does not change the source netplan YAML. This
requires v257's ID_NET_NAME_ONBOARD=end0/end1 and existing net.ifnames=0.
The generated configuration retains Name=e* and adds per-alias Property matching.
Missing/invalid data or devices cause an explicit failed service and journal
error; networkd has Wants/After, not Requires, so its existing DHCP fallback can
still start. A runtime MAC command is not intended for a running network.
The configuration semantics are documented in
[systemd v257](https://github.com/systemd/systemd/blob/v257/man/systemd.network.xml).

Kernel and DTB holds remain required. No automatic FIT updater is installed.
`apt-mark unhold`, kernel installation and manual `update-initramfs` are not a
supported factory upgrade: regenerate/audit the matching kernel, DTB, modules,
initrd and FIT/root TAR together before deploying an independently approved
upgrade. Ordinary /boot changes do not update the raw FIT kernel partition.

Patch 902 changes only E87N: RAM 0x40000000+0x40000000; ramoops
0x7ff70000+0x10000 with one explicitly chosen 64 KiB record; secmon
0x7ff80000+0x80000 with no-map. It retains the inherited wmcpu
0x50000000+0x100000 reservation. The compatible spelling is `ramoops`, per the
[Linux binding](https://github.com/torvalds/linux/blob/v6.18/Documentation/devicetree/bindings/reserved-memory/ramoops.yaml).
RAM placement and actual first boot still require the complete artifact audit.
