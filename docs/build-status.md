# Build status — 2026-09-12

> Historical build log, superseded by the [2026-09-13 display/fan candidate](candidate-display-fan-20260913.md). References below to 12 patches or an unsupported display describe the older images, not the current 13-patch source tree. Local artifacts and logs mentioned here are excluded from Git; see [artifact availability](DOWNLOADS.md).

The current target is **Debian 13 Trixie + Linux 6.18.51 LTS**. The family now
pins the official stable-kernel commit
`f6388029ea9e2c9e807d73827658738ea131faee`, uses `edgepi-e87n-6.18` and
`linux-edgepi-e87n-lts`, and has an integrated **12-patch** board series.
Targeted ARM64 compilation has passed. The initial full compilation started at
21:40:35 CST on 2026-09-12 and was intentionally superseded, with its object
cache preserved, after review found PHY resource/firmware error-path issues.
The final-input build completed under invocation
`282bd69e0665409cb6137de1d30b8046`, using the corrected PHY patches,
`BSPFREEZE=yes` and `CLEAN_LEVEL=none`.
At 23:16:03 CST the final-input build completed the kernel link and generated a
20,103,680-byte ARM64 `Image`. Module compilation has also finished; at 23:20
CST the build entered kernel/DTB/header Debian-package generation. The full build
finished successfully at **23:25:03 CST on 2026-09-12**, with `active (exited)`,
`Result=success`, `ExecMainCode=1` and `ExecMainStatus=0`. `ExecMainCode=1` is
the normal-exit category, not a process exit status of 1.
**The real Debian 13.6 Trixie / Linux 6.18.51 candidate has been generated and
passed the real read-only static image audit.** Its installed kernel release is
`6.18.51-current-filogic`. Xz compression and the complete export to the Mac have
finished. `xz --test` passed in both the VM and on the Mac; the Mac compressed
SHA-256 and streamed decompressed raw SHA-256 both match the VM results.
The release `SHA256SUMS` passed in full, and all 52 host source-build-inputs
entries match the VM manifest. Local static-candidate delivery is complete.
This is not board-boot, all-driver or production-security acceptance.

`RELEASE=trixie` pins the target Debian release; packages within that release
still receive updates from signed repositories. The Armbian framework remains
pinned to `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`. Neither the kernel nor the
framework follows a floating branch. The prepared Trixie ARM64 rootfs cache is
an input, not a bootable E87N image. Linux 7.2.5 was an exploratory source download
and read-only review only: it was never compiled and is not this build's target.

## Current LTS integration and limits

- The main build has confirmed all 12 patches apply to the real 6.18.51 baseline
  with `--fuzz=0`. The DTB compiles with 7 retained warnings about legacy node
  structure; this is not a clean full-schema validation result.
- Targeted ARM64 objects passed for MAC, PCS, two PHY drivers, PWM, LVTS, PCIe
  and the CPUFreq-dt blocklist. The five clock objects and pinctrl had already
  passed. These were targeted compile results, not just patch-parser checks;
  the full build and real image audit have since passed separately. Compiling the
  CPUFreq-dt blocklist does not enable or validate DVFS.
- The first full-build attempt failed because of Mac AppleDouble metadata files.
  After a clean input transfer, the retry passed Armbian's actual parsing and
  application of all 12 patches, then began full compilation at 21:40:35 CST.
  This records recovery from the input problem; the final-input build completion
  and separate real audit are recorded above and below.
- The final `.config` for this attempt has been checked: thermal, PWM fan,
  efuse, USB-root and MMC requirements are built in; `CPU_FREQ=n`,
  `CPU_THERMAL=n`, `MEDIATEK_2P5GE_PHY=m` and `MTK_NET_PHYLIB=y`.
  Configuration checks do not prove runtime driver or board behavior.
- The revised PHY caches pinctrl/state during probe instead of acquiring another
  managed reference on every initialization. Both firmware files are requested
  and size-checked before hardware writes. The revised object passed native
  ARM64 compilation, and all 12 patches plus the DTB passed again. These checks
  do not prove runtime firmware loading, recovery or Ethernet operation.
- The 12 patches cover the board DTS, pinctrl/clocks, Ethernet MAC/PHY/PCS,
  PCIe, PWM, LVTS and the CPUFreq safety restriction. This is a different series
  from the 17 patches used for the historical 6.12.108 image below.
- CPU DVFS and CPU cooling are disabled for this prototype. CPU OPP and CPU
  cooling-map references are removed; the board retains its firmware-set boot
  frequency without Linux CPUFreq frequency/voltage transitions. The old common
  850 mV OPP values and missing `proc-supply` are not validated supply data.
  The generic CPUFreq auto-registration path is blocked, and this attempt's
  final config confirms the disabling policy; CPU throttling is not available.
- **MT7987 WED is unsupported** and is not registered. MAC/PHY/PCS integration
  does not establish WED hardware-offload support or measured Ethernet operation.
- LVTS uses software polling, with both normal and passive intervals set to
  1000 ms; no physical IRQ is guessed. Invalid or missing calibration is rejected.
  A failed sensor probe means temperature protection has not been established.
- Fan levels are `<0 128 192 255>`, with active trips at 50/65/75 degrees C mapped
  to fan states 1/2/3, plus a critical trip. These are software control thresholds,
  not chip limits. All inherited CPU cooling references must be absent so a
  missing provider cannot prevent thermal-zone registration. This avoids the
  old 26-level table/1-2-3 map mismatch and does not depend on an OpenWrt fan daemon.
  The thermal/PWM fan chain should be built in; actual sensor accuracy, fan spin-up,
  RPM, heat dissipation and critical-trip behavior remain untested on the board.
- **The NV3007 small display is unsupported and awaits a driver port.** The
  vendor `999990-fbtft` patch is absent from both this 6.18.51 series and the old
  6.12.108 series. A display node in the DTS or a compiled PWM object does not
  prove display support; backlight behavior is not guaranteed either.
- The vendor USB `auto_load_valid` extension property is ignored by the 6.18
  driver. Keeping it in the DTS does not port or validate the vendor calibration
  flow. U-Boot RAM fixup is also unverified: actual RAM size, the memory layout
  passed to Linux and the final detected capacity require real boot logs.
- Original fixed MAC addresses have not been restored. Factory MAC references
  were removed; temporary random MAC fallback and lack of persistence mean old
  MAC-based DHCP reservations cannot be assumed to work.
- Board boot, Ethernet, eMMC, USB, NVMe, suspend/resume and reboot remain untested.
  The target is not an OpenWrt root filesystem. A safe permanent eMMC installation
  still depends on the actual bootloader, partition layout and backups.

## Current candidate: actual static evidence and delivery state

The generated raw disk image is
`Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img`,
**1,149,239,296 bytes**, SHA-256
`1697307756412aef6fa4cabd33bb4c115daa78f80d81e43233a689a3e962f6bc`.
The actual target filesystem identifies Debian **13.6 Trixie**, not just a
Debian 13 build host or rootfs cache.

- Real whole-disk `verify-image --release trixie --require-usb-root` passed GPT,
  ext4 `fsck -fn`, root/boot UUID consistency, firmware and initrd USB-root checks.
  The real Debian packages and the final installed DTB/config also passed their
  checks. No target image code was executed and no device was flashed.
- Read-only inspection confirmed linux-image, DTB, BSP and base-files are held.
  Their `Package`, `Version` and `Armbian-Original-Hash` match the unique respective
  `.deb` from this final build. No `linux-u-boot` package or unexpected user SSH
  key was found; shadow contained no empty password fields. This does not imply
  strong passwords or a complete security audit.
- The actual `10-dhcp-all-interfaces.yaml` uses networkd and requests IPv4/IPv6
  DHCP on `e*`, `lan*` and `wan*`. `serial-getty@ttyS0` is enabled, with a generic
  getty override for root autologin. These are file-level checks, not proof of a
  successful serial login, Ethernet link or DHCP lease on E87N.
- **Isolated first boot is mandatory.** The image retains `root/1234`, serial
  autologin and permitted SSH root login. Cached shared initial SSH host keys
  remain; first-boot regeneration is ordered `After=ssh.service`, so unique keys
  cannot be assumed before the first SSH connection. Change the password and
  verify key regeneration/fingerprints before exposing the board to untrusted
  networks. Automatic root expansion targets only the current root disk.

The `.img.xz` compression is complete: **176,462,804 bytes** (about 168.3 MiB),
SHA-256 `32474999d280d4a9057985c6ba6985b1ed5f223584b7f8fe1809f9e4f48cb33e`.
The complete output has arrived in the actual Mac release directory
`output/releases/2026-09-12-e87n-trixie-lts-6.18.51/`. `xz --test` passed on the
Mac as well as in the VM. The Mac compressed-file SHA-256 matches the value
above, and `xz -dc | shasum -a 256` produced the same raw SHA-256 recorded above.
The transfer archive checksum also matched. These are completed local-delivery
and image-integrity checks, not hardware tests. The release `SHA256SUMS` was
generated and every entry passed `shasum -a 256 -c`. All 52 host
source-build-inputs entries match the VM manifest. On the Mac,
`xz --robot --list` confirmed compressed/uncompressed sizes of 176,462,804 /
1,149,239,296 bytes and CRC64 integrity was verified. See the
[new candidate record](candidate-trixie-6.18.51-20260912.md) for exact component
hashes, UUIDs, package hashes and the delivery handoff, and
[the upgrade record](upgrade-trixie.md) for the pin and acceptance criteria.

## Historical Bookworm / Linux 6.12.108 build

The following history and its successful compilation/image checks apply only
to the old Debian 12 Bookworm / Linux 6.12.108 candidate. That image was built
at 19:54 CST and passed the real read-only audit, including USB-root initramfs
modules. It has NOT been booted or hardware-tested on E87N. Its files and original
checksums are retained under `output/releases/2026-09-12-e87n-bookworm/`; see
[the historical candidate evidence](candidate-20260912.md). These results do not
validate Debian 13 or Linux 6.18.51.

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
At 18:29 CST the 261 MiB kernel cache passed object validation. Build-host
compiler dependencies finished installing at 18:37. The first build exited 1
because upstream Python setup embedded literal quotes in PATH, so pip could not
find `/usr/bin/git`. Git was installed; this was not a kernel source failure.

The one-line fix is recorded in `patches/armbian-build/0001-python-env-path.patch`
and applied idempotently by `scripts/prepare-framework.sh`. A regression using
the real framework assignment and command runner failed before the fix, passed
after it, and the second application was a no-op. Framework HEAD is still pinned;
its working tree now intentionally includes this documented fix.

The same terminated build unit was restarted after the fix. Python dependencies
completed, and at 18:42 CST Armbian applied all 17 hardware patches to the actual
kernel worktree. ARM64 GCC 14.2 then completed Kconfig and began compiling kernel
objects. At 18:44 CST every board-requested early-boot `=y` setting and the
MediaTek 2.5G PHY `=m` request was verified in the final `.config`; PCIe/NVMe are
built in and the MediaTek xHCI driver is modular. The configuration snapshot is
retained locally at `output/runtime/kernel-final-6.12.108.config`.

At this early stage no complete kernel packages, rootfs or image had been
validated; later compilation and image results are recorded below.

At 19:03 CST the same live unit had progressed into networking and device-driver
compilation. Readelf verified the actual `pinctrl-mt7987.o` and all five MT7987
clock objects as AArch64 relocatables. No new compiler error had occurred.
The final config also enables MediaTek CPU frequency control, both thermal
drivers, PWM fan support and USB storage; these are configuration/object checks,
not full-link, initramfs, network or hardware validation.

At 19:15 CST, readelf also verified the compiled MediaTek Gen3 PCIe and 2.5G PHY
objects as AArch64. The PHY object's `.modinfo` requests both MT7987 firmware
paths shipped by this repository. These are actual compiler outputs, but the
kernel link and image build are still pending; no Ethernet or PCIe hardware
functionality has been established.

The Ethernet SoC object failed at 19:16 CST: patch 750 used `DESC_SIZE`, a macro
from OpenWrt's separate descriptor-shift optimization that the pinned BPI kernel
does not have. The other make subtrees continued compiling, so a running unit
was not proof that this build could succeed. The initial journal view also hid
GCC's ANSI diagnostics as blob data; `journalctl --all` recovered the actual
errors, and the log helper now always preserves them.

Patch 750 now initializes `.desc_size = sizeof(...)` for both MT7987 descriptor
types, matching the baseline's MT7988 data and byte-size arithmetic. All 17
patches passed fuzz-zero application and actual Armbian parsing again. The
known-failed build was deliberately stopped, its caches retained, and the old
750 patch backed up in the VM before copying the checked replacement. systemd
unloaded the stopped transient unit, so the guarded launcher recreated the same
unit name at 19:26 CST. Armbian applied the corrected patch series using the
existing kernel worktree. At 19:29 CST the corrected `mtk_eth_soc.o` passed
readelf's ELF64/AArch64 check and the MediaTek Ethernet directory produced its
`built-in.a`. The final `.config` SHA-256 is unchanged from the checked snapshot.
This resolved that observed compile error; the full build continued. Ethernet
hardware remains untested.

At 19:45 CST the real kernel passed MODPOST and completed the `vmlinux` link.
`arch/arm64/boot/Image` is 17,134,080 bytes and is identified as an ARM64 boot
Image; `vmlinux` is a statically linked AArch64 ELF executable. The actual
22,284-byte DTB contains `edgepi,e87n`, `mediatek,mt7987a` and `mediatek,mt7987`.
Compiled thermal, USB controller/storage, CPU frequency, MMC, PWM and EFUSE
objects were also checked as AArch64 relocatables. Module completion and Debian
packaging continued; these files alone were not the final disk image.

At 19:50–19:52 CST kernel/module compilation, installation and packaging
completed. The real kernel/DTB packages passed `verify-artifacts.sh`, first in
`output/packages-hashed/global` and again after framework reversioning into
`output/debs` as version `26.11.0-trunk`. The checked kernel release is
`6.12.108-current-filogic`, with Image SHA-256
`b7e21e3213782d2ba4dd911b508fd54ac5ae5a6ab6af4ed5d3df114e3059fcbd`.
This verified the Image header, final kernel configuration, E87N DTB and actual
compressed AArch64 PHY module; it did not verify a root filesystem or initrd.
Armbian then extracted its Bookworm ARM64 minimal rootfs cache and installed the
kernel, DTB and E87N BSP packages.

At 19:54 CST the unit completed normally: `active(exited)`, `Result=success`,
`ExecMainCode=1`, `ExecMainStatus=0`. It produced
`Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_bookworm_current_6.12.108_minimal.img`.
The original image SHA-256 sidecar verified before and after the read-only audit.
The first audit exposed an overly strict verifier rule: sgdisk reported a valid
GPT plus its standard fdisk first-usable-LBA 2048 alignment notice. The verifier
now permits only that exact notice when the parsed GPT agrees; other warnings,
corruption, layout mismatches and boundary failures still fail.

The real whole-image audit then passed: expected GPT, clean ext4 filesystems,
Debian Bookworm identity, checked PHY firmware/licence, matching Image/DTB/module
release, and one root UUID matching extlinux and fstab. The actual root UUID is
`738f4a31-6e60-4080-a354-4e23d1efeef9`. The selected initramfs was parsed by host
tools and contains `/init` plus nine required USB-root modules/dependencies.
Audit directory `/tmp/e87n-image-audit.OrYsZal2` is retained in the VM; its owned
loop and mounts were released. No image or physical device was flashed.

An additional read-only check found AArch64 systemd/Bash, the `ttyS0` serial
getty enablement, systemd-networkd enablement and the expected Ethernet DHCP
netplan file inside the actual image. These are startup-file checks, not a
measured login or network result. The compressed candidate is 166,285,388 bytes;
the decompressed disk image is 1,124,073,472 bytes. See
[candidate notes](candidate-20260912.md) for checksums and the board-test handoff.

The build VM output location is `/srv/e87n/source/armbian-build/output/`.
The launchers have no device-flashing step.

`build-lima.sh` follows the actual conventional service rather than treating
successful state queries as completed builds. Its 37 mocked orchestration tests
passed; read-only checks against the real failed unit returned 1, and against the
resumed running unit returned 75. The completed build now returns 0 with a fresh
image. The dedicated compressed candidate was copied to the Mac release folder;
its SHA-256, local xz integrity and streamed decompressed SHA-256 all match the
VM results. A separate full-output snapshot copy was also started; a `.partial`
snapshot must never be treated as a completed export or a release image.

## Historical 6.12.108 changes and checks

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
  serial/ext4 drivers. The actual final Kconfig satisfies these requests; the
  full compile/link passed. Hardware behavior still needs validation.
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
  including malformed/missing artifacts and root-parameter errors. The actual
  kernel/DTB packages and mounted read-only candidate filesystems also passed.
- Added a Linux-only whole-image audit: clean GPT, expected layout, new read-only
  loop, ext4/fsck, true root UUID, artifact checks and an initramfs listing.
  Its device/mount operations passed 46 mocked cases, including the narrow
  fdisk alignment-notice rule and rejection of corruption or mismatched GPT
  metadata. The actual image was also mounted read-only and passed the audit.
  The board requests host `initramfs-tools-core` for the host-side listing.
- Added optional `--require-usb-root` validation of the actual boot config,
  matching-release USB/SCSI/T-PHY modules and recursive dependencies in the
  initramfs listing. Its 41 read-only synthetic fixtures passed, including the
  final config snapshot with a synthetic listing. The real image's initramfs
  then passed with nine required modules/dependencies. This does not establish
  U-Boot USB access or hardware operation.
- Removed the old squashfs/f2fs bootargs and factory MAC NVMEM references from the
  E87N DTS. The generic GPT lacks factory; this prototype allows temporary random
  MAC addresses instead of deferring the Ethernet probe indefinitely.

## Re-running checks for the current LTS pin

These commands use the current family pin and patch directory; their presence
is not a record of a successful new build. Supply a Linux stable Git checkout
containing the pinned 6.18.51 commit, rather than assuming the old BPI checkout
contains it. To audit the historical series, explicitly pass its old commit and
`edgepi-e87n-6.12` as the second and third arguments to the patch-check script.

```sh
bash tests/test-launcher.sh
# Bash 5 / builder container:
bash tests/test-board-config.sh
# Linux uses GNU patch; on macOS set PATCH_BIN to gpatch:
bash scripts/check-kernel-patches.sh /path/to/linux-stable
# Optional DTB compile (C preprocessor, dtc and fdtget required):
CHECK_DTB=yes bash scripts/check-kernel-patches.sh /path/to/linux-stable
# Uses the checked-out Armbian parser; uv supplies isolated Python dependencies:
uv run --script tests/test-patch-discovery.py
```

## Source revisions inspected

| Source | Revision |
| --- | --- |
| Armbian build | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| Current official Linux 6.18.51 LTS | `f6388029ea9e2c9e807d73827658738ea131faee` |
| Historical BPI Router Linux 6.12.108 | `b864732ee285e7868fb0857d69a8ff349e37003e` |
| E87N reference source | `c51dcd733aeda3c24c730ddaff2c54440a72ca6d` |
| Linux firmware | `c0af6c70df291701fdecf6402e47dd4564e6b718` |

The current kernel and Armbian framework use the explicit pins above. Both the
historical Bookworm / 6.12.108 candidate and the separate new Debian 13.6 Trixie /
6.18.51 candidate have passed full compilation, packaging and real static image
audits. The final LTS build completed at 23:25:03 CST; xz compression, full Mac
export, xz integrity and the local compressed/decompressed SHA-256 checks have
also completed successfully. The 7 legacy DTB structure warnings remain.
Board boot, driver/hardware validation, isolated first-boot credential/key changes
and a safe eMMC installation plan are still required. Neither candidate is
certified production-ready. Prerequisites and destructive-write boundaries are in
[first-boot.md](first-boot.md).
