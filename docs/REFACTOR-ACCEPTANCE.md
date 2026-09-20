# Maintained MT7987 kernel and container acceptance

This work replaces the previous independent MT7987 SoC backport with the
maintained Frank-W 6.18 LTS tree. The reviewed versions and immutable commits
are recorded in `userpatches/config/e87n-build.json`. E87N wiring, memory,
factory partition layout, display and fan policy remain board-specific.

The requested deliverables are a complete Debian 13 minimal Armbian image,
the existing-loader firmware archive, an independently installable display
Debian package, and reproducible build/test evidence. The workflow remains a
single manual dispatch without mandatory inputs and generates its release tag.

## Required evidence before completion

| Requirement | Evidence required |
| --- | --- |
| Latest reviewed Debian 13 and 6.18 LTS | official release lookup, immutable source pins, actual package/guest versions |
| Maintained MT7987 implementation | fixed Frank-W checkout, E87N-only patch set, real patch and DTB compilation |
| Independent E87N kernel package identity | actual Debian image/DTB packages and matching kernel/module release |
| Complete build | clean compiler exit, image, firmware TAR, display deb, hashes, source/config provenance |
| Docker reproducibility | Dockerfile and executable launcher, native Linux workspace, retained logs and real exit status |
| Same kernel and production rootfs in simulation | QEMU inside Docker with target Image/initrd and a private rootfs copy, identity/hash report |
| Real init/service management | guest PID 1 is systemd; ssh/network services active; no first-login provisioning prompt |
| Requested defaults | root password SSH login, DHCP lease, DNS, APT update/install, Asia/Shanghai, zh_CN.UTF-8 |
| Reboot/persistence | simulated reboot returns with persisted state and host keys, subsequent cold boot works |
| Minimal package policy | no desktop, LuCI, Docker daemon or unsolicited large storage management stack |
| Display package lifecycle | package build and install/upgrade/remove checks; hardware display test remains pending |
| Release gate | collection/publication requires matching successful simulation evidence; manual-only workflow |
| No premature hardware claims | MT7987 peripherals, real U-Boot handoff and installation explicitly pending |

Docker containers share the Docker host kernel. Container `uname`, chroot or
shell fixture checks alone do not satisfy the target-kernel requirement. QEMU
`virt` can boot an ARM64 Linux kernel and run Debian/systemd with emulated block
and network devices; it does not model the E87N MT7987 MAC/PHY, MMC, SPI display,
PWM fan, calibration data or board watchdog. Successful simulation is therefore
the software gate before, not a substitute for, later hardware acceptance.

Physical device operations are deferred during this phase. Existing diagnostic
images and source/build caches are retained; no historical artifact is relabelled
as a new successful build.
