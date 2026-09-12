# Build status — 2026-09-12

No bootable image has been produced or board-tested. The target remains Debian
Bookworm / Armbian, not an OpenWrt root filesystem.

## Build environment

The interrupted official image pull never reached Armbian compilation. A new
native ARM64 Debian 13.6 container started successfully:

```text
e87n-armbian-20260912T084122Z-83129
source/armbian-build/output/logs/launcher-20260912T084122Z-83129.log
```

Its bootstrap downloaded 33.7 MB of dependencies, then `dpkg` became blocked in
uninterruptible disk I/O while unpacking perl-base. Reading that process's kernel
stack showed `jbd2_log_wait_commit → ext4_sync_file → ovl_copy_up_metadata`.
There was free disk space, and container inspection reported no OOM kill.
This was a Docker VM/storage stall, not a kernel compiler result. It subsequently
resumed unpacking without restarting Docker. After installing basic tools, it
spent over 23 minutes in the framework's `git describe --dirty` scan. The Docker
VM also reported high CPU pressure. It had not started kernel compilation.

The E87N container was deliberately stopped and retained for a build-host
migration (exit 137 is not a kernel compiler result). Docker also hosts unrelated running
projects: **do not restart Docker or stop those projects without approval**.

The active build has moved to a separate Lima 2.2.0 ARM64 Debian 13 VM, named
`e87n-armbian`, with 4 CPUs, 8 GiB RAM and a 64 GiB sparse disk. The pinned image
and its SHA-512 are in `scripts/lima-e87n.yaml`; it shares no host directories,
SSH agent, Docker socket or physical devices. Port forwarding other than its
local SSH connection is disabled. No Docker daemon restart was performed.

The same framework scan completed in 1.833 seconds there. Input archive SHA-256
`b433d2bf47fcbb86995cd2b3b4560d55947f88a885110182855820d8a05b3fe8`
matched after transfer; it was unpacked into `/srv/e87n` on the native Linux disk.
The framework's tracked tree is clean and the board-loader test passed again.

At 18:26 CST, the retained systemd unit `e87n-armbian-build.service` started the
fixed-commit kernel cache preparation followed by the full Armbian build.
`scripts/seed-kernel-cache.sh` uses a depth-one, unfiltered fetch of the actual
BPI kernel commit and verifies Git objects before marking the cache ready. It
does not patch the kernel, bypass compilation, or replace an existing cache.
At 18:29 CST the 261 MiB kernel cache passed object validation, and Armbian
started installing build-host dependencies. Kernel compilation and image
validation remain pending. There is still no final rootfs or image to test.

The active VM output location is `/srv/e87n/source/armbian-build/output/`.
The launchers have no device-flashing step.

## Changes and checks

- Restored the full Armbian checkout; the earlier sparse checkout lacked required
  directories such as `config/templates`.
- Added native Linux cache/temp volumes, bounded build resources, argument
  forwarding, overlay backups, detached execution and exit-code propagation.
- Isolated the E87N family so the upstream Filogic MT7987 rejection is not reached;
  removed the copied raw BL2/FIP writer from this project's active family.
- Moved the patchset to `userpatches/kernel/edgepi-e87n-6.12`, the path used by
  Armbian's actual Python patch discovery. The former extra `patch/` component
  would have hidden the hardware patches from the builder.
- Corrected the root command line and requested early-boot storage/clock/pinctrl/
  serial/ext4 drivers. The full `olddefconfig` and compile still need validation.
- Added two unmodified MT7987 PHY firmware blobs with upstream provenance,
  MediaTek redistribution licence, size checks and SHA-256 verification.
- Passed launcher mock tests and shell syntax checks.
- Passed `tests/test-board-config.sh` inside the ARM64 container using the actual
  Armbian family/architecture loader. This is a configuration test, not a build.
- Firmware SHA-256 and expected sizes passed local verification.
- Ported the PHY/LED/PCS patches to the baseline's actual APIs and file layout,
  added the missing thermal/cpufreq prerequisites, and checked all **17 patches
  with GNU patch and `--fuzz=0`** in an isolated tree. All applied. This is not
  evidence that the resulting kernel compiles or boots.
- The actual Armbian Python patch-discovery and parsing classes now find and
  parse all 17 patches. Normalized multi-file addition headers to avoid the
  parser's repeated `/dev/null` ambiguity.
- Independently compiled the E87N DTB using the pinned kernel's binding headers;
  checked the root compatible and memory type. Added missing `device_type` on
  memory and SPI address/size cells. DTC still reports vendor-source node naming
  warnings; a complete schema check and hardware validation have not run.
- Kept the reference DTS's conservative 256 MiB memory range. The actual board
  RAM size and U-Boot memory fixup still need to be established from boot logs.
- Added patch 742 to allow the integrated MT7987 PHYA without an external `phys`
  phandle. Explicit PHY references still propagate errors/deferred probes;
  MT7988's required external PHY path is unchanged. This fixes a source-level
  PCS probe failure, not a measured Ethernet test.
- Made watchdog/reset, syscon and EFUSE/NVMEM configuration requests explicit.
- Added a read-only artifact verifier for matching kernel/DTB packages or
  extracted Debian rootfs/bootfs. Its 60 synthetic fixture checks passed,
  including malformed/missing artifacts and root-parameter errors. No real
  kernel packages or system image have yet been validated with it.
- Added a Linux-only whole-image audit: clean GPT, expected layout, new read-only
  loop, ext4/fsck, true root UUID, artifact checks and an initramfs listing.
  Its device/mount operations passed 34 mocked cases; no real image has been
  mounted or validated. The board requests host `initramfs-tools-core` for this
  audit, not as a substitute for the actual target-system checks.
- Removed the old squashfs/f2fs bootargs and factory MAC NVMEM references from the
  E87N DTS. The generic GPT lacks factory; this prototype allows temporary random
  MAC addresses instead of deferring the Ethernet probe indefinitely.

Repeat the checks with:

```sh
bash tests/test-launcher.sh
# Bash 5 / builder container:
bash tests/test-board-config.sh
# Linux uses GNU patch; on macOS set PATCH_BIN to gpatch:
bash scripts/check-kernel-patches.sh /path/to/BPI-Router-Linux
# Optional DTB compile (C preprocessor, dtc and fdtget required):
CHECK_DTB=yes bash scripts/check-kernel-patches.sh /path/to/BPI-Router-Linux
# Uses the checked-out Armbian parser; uv supplies isolated Python dependencies:
uv run --script tests/test-patch-discovery.py
```

## Source revisions inspected

| Source | Revision |
| --- | --- |
| Armbian build | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| BPI Router Linux 6.12-main | `b864732ee285e7868fb0857d69a8ff349e37003e` |
| E87N reference source | `c51dcd733aeda3c24c730ddaff2c54440a72ca6d` |
| Linux firmware | `c0af6c70df291701fdecf6402e47dd4564e6b718` |

The kernel and default Armbian build checkout are now pinned to the revisions
above. Compile, artifact and hardware validation are
still required before release. Further prerequisites and destructive-write boundaries are in
[first-boot.md](first-boot.md).
