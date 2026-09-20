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

## Final TAR evidence (format v2)

The converter and final TAR verifier now read the actual root payload, without
requiring extlinux or a display package for the headless profile. They require
the independent `linux-image-current-edgepi-e87n` and
`linux-dtb-current-edgepi-e87n` packages to be held, ARM64 and the same Debian
package version. The image/config/DTB/initrd filenames use the release from the
central build configuration; the Image's embedded Linux banner and every
installed module's ARM64 ELF `.modinfo`/vermagic must agree with that release.
The final audit checks modules.dep coverage and ELF dependencies, soft
dependencies, the required MediaTek PHY/shared-library modules, and uses host
`modprobe --show-depends --ignore-install --config /dev/null` to check the shipped
binary indexes for those PHY modules. No module is loaded or target program run.
Host kmod and zstd (for zstd-compressed modules) are required in the audit container.

The two required PHY blobs and their licence are compared against the reviewed
repository `firmware/SHA256SUMS`, rather than trusting checksums supplied by the
same rootfs. Module and firmware tree digests also cover other files, modes,
ownership and symlink targets. The initrd is bound byte-for-byte to the FIT;
its module listing must use the same release and include the dependency closure
for each listed module. A bounded in-memory newc parser also compares each
initrd module's decompressed ELF bytes to the installed module, including when
initramfs-tools changed module compression. Any MT7987 PHY blobs in initrd must
also match the independently audited rootfs blobs. It supports uncompressed early cpio
plus gzip, xz or zstd archives (256 MiB expanded audit limit); it never extracts
archive paths to the filesystem. Unexpected formats, duplicate module entries,
hardlinked modules without data, or missing dependencies fail closed. Static
dependency/ABI checks do not prove successful driver probing.

Build inputs must include two receipts, created by the build pipeline (never
synthesized from the converter's current working tree):

- `/usr/share/e87n/build-provenance.json`: schema 1, `build`, `source_files`,
  `recipe_sha256`, `source_commit`, `source_dirty` and `hardware_validation=pending`.
  The recipe digest is SHA256 of the canonical JSON source-file hash map.
- `/usr/share/e87n/build-recipe.json`: schema 1, `kernel_release`, `kernel_source`,
  `kernel_commit`, `armbian_commit`, `source_commit`, boolean `source_dirty`,
  `recipe_sha256`, ordered `patches` (`path`/`sha256`), `kernel_config_sha256`
  and `kernel_sha256`. The build hook records the actual compiled Image/config;
  both hashes are checked against the input files. Optional
  `patched_kernel_commit` and `patched_kernel_tree` are retained. Patch paths
  relative to userpatches are matched to the source map with that prefix added.

Dirty sources are accepted only with explicit, matching dirty state and recipe
digest in both receipts. A Git HEAD alone is not the complete source identity.
The recorded source/kernel/framework commits are build-hook claims bound to the
artifact, not independently reconstructed from binaries or cryptographically
authenticated provenance. Missing, stale or inconsistent receipts fail closed;
old v1 TARs must be checked with their historical auditor rather than relabelled.

CONTROL records observed `/etc/debian_version`, the installed dpkg package
versions/architectures/states and database hash, receipt hashes, ordered patch
digest, and final Image/config/DTB/initrd/modules/firmware evidence. A canonical
SHA256 `build_id` covers all CONTROL fields except itself, including profile,
root UUID, source-image hash and final FIT/root payload hashes. It is stored
outside rootfs to avoid a circular root-image hash. CONTROL is limited to 2 MiB.
The final verifier independently recomputes the evidence after repacking.
The content ID and FIT hashes detect inconsistency, not authenticity against an
actor able to replace both payloads and manifest; retain the trusted external
TAR hash/release evidence.

This remains a complete firmware-set update contract, not an online p4 writer,
atomic A/B updater or rollback mechanism. Package/initramfs changes can leave
`/boot` out of sync with the raw FIT despite kernel holds. They require a fresh
matching firmware build and audit. Static/fixture PASS is neither container
integration nor QEMU/PID1 boot acceptance. QEMU cannot validate the board's MTK
drivers, factory MACs, eMMC expansion, watchdog or original U-Boot handoff;
hardware acceptance remains pending and the live-board guards are unchanged.

Patch 902 changes only E87N: RAM 0x40000000+0x40000000; ramoops
0x7ff70000+0x10000 with one explicitly chosen 64 KiB record; secmon
0x7ff80000+0x80000 with no-map. It retains the inherited wmcpu
0x50000000+0x100000 reservation. The compatible spelling is `ramoops`, per the
[Linux binding](https://github.com/torvalds/linux/blob/v6.18/Documentation/devicetree/bindings/reserved-memory/ramoops.yaml).
RAM placement and actual first boot still require the complete artifact audit.
