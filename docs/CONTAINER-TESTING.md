# Final-firmware Docker/QEMU acceptance

Run from the repository root on a native ARM64 Docker host. No KVM, privileged
container, loop devices, physical E87N or LAN SSH target is used by this runner.
The container is built independently from Debian 13.7 slim pinned by digest;
it does not require the local firmware builder image.

The `--display-deb` input is the same-source baseline package produced for and
embedded in this firmware build. QEMU uses it to validate the firmware's
preinstalled display baseline and package lifecycle. It is not a requirement
that a later independently published Display Release use the same run, tag,
version or SHA-256; Display updates do not require rebuilding the firmware.

```bash
bash testing/run-container.sh \
  --firmware /absolute/path/final-uboot-firmware.tar \
  --display-deb /absolute/path/e87n-display_1.1.0-1_all.deb \
  --output /absolute/path/new-qemu-result-directory \
  --source-commit "$GITHUB_SHA" \
  --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT"
```

All six arguments are required. The source SHA must match the provenance and
compiled recipe **inside this firmware**, not merely the current checkout.
For a local run, use its actual 40-character source SHA and a numeric local run
ID/positive attempt. Each invocation needs a fresh output directory without
`result.json`. TAR/deb input mounts are read-only. The final TAR must satisfy the
existing factory contract, including the 768 MiB upload limit.

For immediate development inside the existing ARM64 builder (uses the current
read-only `/src` checkout, not the potentially older `/work/repo/testing`):

```bash
docker exec e87n-refactor-builder-20260920 \
  python3 /src/testing/qemu_runner.py \
  --firmware /out/final-uboot-firmware.tar \
  --display-deb /out/display-a/e87n-display_1.1.0-1_all.deb \
  --output /out/qemu-acceptance-attempt-1 \
  --source-commit "$SOURCE_SHA" --run-id 20260920 --run-attempt 1
```

Use the actual finalized TAR path. An Armbian `.img` or unfinished root tree is
not an accepted substitute. Final verification should use the public host CLI
above so the standalone image/dependencies are exercised too.

## What actually runs

`factory_firmware.inspect_tar` verifies the final TAR, CONTROL, FIT hashes and
board contract. The runner extracts its LZMA kernel into an ARM64 `Image` and
uses the FIT's **unchanged original initrd**. It verifies that unbooted root has
no SSH host keys, then copies its ext4 bytes before any guest writes.

QEMU uses `virt,gic-version=3`, `cortex-a53`, two CPUs, 1 GiB RAM, native
`tcg,thread=multi`, PL011 console, `virtio-blk-device` and
`virtio-net-device` (virtio-mmio). QEMU supplies the virt DTB; the factory DTB is
validated as part of the FIT but is not suitable for virt hardware. The kernel,
initrd, userspace and systemd are the production artifact's own files.
`root=UUID=...` preserves the original filesystem identity and fstab.

Only these hardware-specific services are masked, using kernel command-line
`systemd.mask=` parameters:

* `e87n-factory-mac.service`: requires E87N DT aliases and factory p2 MAC bytes.
* `e87n-factory-resize.service`: requires physical `/dev/mmcblk0p5`.

The runner does not change guest systemd services, SSH configuration, password,
network configuration, fstab or host keys to make boot pass. `net.ifnames=0`
provides an `eth0` name matching the production netplan `e*` rule. QEMU user
networking supplies DHCP/DNS and forwards a random **container-loopback-only**
SSH port; the wrapper publishes no host port.

Guest SSH accepts only root password authentication with the public factory
password `doumao`; a PTY login shell must complete without a first-login setup
prompt. The acceptance script checks:

* Actual guest `uname`, ARM64, systemd PID 1, `/dev/vda` ext4 root, installed
  Image/initrd SHA matching the FIT and source provenance/recipe matching SHA.
* Networkd's actual DHCP lease/address, DNS lookup, Shanghai timezone, UTF-8
  login charmap with `LANG=zh_CN.UTF-8`, Debian Trixie point version and absence
  of desktop, Docker/containerd/Podman, LVM, mdadm and LuCI packages.
* Signed-source `apt-get update` with any update error fatal, installation and
  execution of `hello`, kernel/DTB holds and `upgrade`/`dist-upgrade` simulations
  that must not install/remove those held packages.
* Exact supplied firmware-baseline display deb SHA, install, same-version reinstall, remove,
  install after remove, and final remove. Locally edited registered conffiles
  must remain byte-identical through the lifecycle and reboots. QEMU does not
  validate the physical display/fan or require the display daemon to work on
  absent hardware.
* Warm guest reboot within the same QEMU process, then clean poweroff and a new
  QEMU process on the same private root. Boot IDs must change while a nonce file,
  SSH public-key fingerprints, conffiles and held package versions persist.
* A second untouched root copy must generate different host keys through the
  firmware's own first-boot services. The runner never invokes `ssh-keygen -A`.

## Reports, timeouts and disk space

`OUTPUTDIR/result.json` begins as FAIL and becomes PASS only after all phases,
input rehashing, `validate_artifact_binding` and the shared CI `validate_report`
pass. It binds TAR, embedded FIT/root, firmware-baseline display deb SHA-256 and
the firmware source/run/attempt. It does not bind future Display Releases.
Failures return a nonzero exit status and retain FAIL plus partial evidence;
the release validator intentionally rejects incomplete or failed results.
Container setup failures also produce a minimal FAIL report.

`evidence.json` records each phase, QEMU argv/PID, boot IDs, host-key fingerprints,
original hashes and any private disk enlargement. Serial logs, SSH/login logs,
APT/package output and `failure.log` are retained. On a guest failure, the runner
attempts to capture failed units, journal, network state and free space. It kills
its QEMU process and removes only its own `mkdtemp` scratch tree. The wrapper
removes its own container on success, failure or interruption.

| Environment | Default | Meaning |
| --- | ---: | --- |
| `E87N_BOOT_TIMEOUT` | 900 | Seconds per SSH boot/reboot wait |
| `E87N_COMMAND_TIMEOUT` | 1200 | Seconds per command; package phase allows at least 3600 |
| `E87N_TOTAL_TIMEOUT` | 7200 | Whole-run deadline in seconds |
| `E87N_ROOT_GROW_MIB` | 1024 | Extra MiB on a private copy only if original free space is below 512 MiB; 0 disables |
| `E87N_KEEP_DISKS` | 0 | Set to 1 to retain private disks in the output directory for diagnosis |

Ext4 growth is offline and only affects private test copies. Both original size
and added size are recorded; the released firmware is never enlarged or
modified. No host/container `uname` or mock result can satisfy guest checks.
Passing this gate leaves physical E87N validation pending.

Runner regressions (real QEMU failure/cleanup, command timeout and private ext4
growth) can run without a firmware artifact:

```bash
docker run --rm --platform linux/arm64 --entrypoint python3 \
  YOUR_RUNNER_IMAGE /opt/e87n/testing/test_runner.py
python3 tests/test-ci-simulation.py
```

These regressions do not produce a firmware acceptance PASS. Use finalized
candidate 2, including the reviewed LVTS fixes, for the actual acceptance run.
