#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/workspace
BUILD="$ROOT/build"
OUTPUT="$ROOT/output"
SOURCE="$ROOT/source/EDGEPI-E87N"
JOBS=${JOBS:-4}

source "$ROOT/config/build.env" 2>/dev/null || true
E87N_SOURCE_URL=${E87N_SOURCE_URL:-https://github.com/ZJJCKA/EDGEPI-E87N.git}
E87N_SOURCE_REF=${E87N_SOURCE_REF:-c51dcd733aeda3c24c730ddaff2c54440a72ca6d}
DEBIAN_RELEASE=${DEBIAN_RELEASE:-bookworm}
DEBIAN_MIRROR=${DEBIAN_MIRROR:-http://deb.debian.org/debian}
ROOTFS_SIZE_MB=${ROOTFS_SIZE_MB:-4096}
BUILD_RAW_IMAGE=${BUILD_RAW_IMAGE:-1}
ROOT_PASSWORD=${ROOT_PASSWORD:-change-me}

mkdir -p "$ROOT/source" "$BUILD" "$OUTPUT"

if [ ! -d "$SOURCE/.git" ]; then
    git clone --depth 1 "$E87N_SOURCE_URL" "$SOURCE"
fi
git -C "$SOURCE" fetch --depth 1 origin "$E87N_SOURCE_REF" || true
if ! git -C "$SOURCE" cat-file -e "$E87N_SOURCE_REF^{commit}" 2>/dev/null; then
    git -C "$SOURCE" fetch --depth 1 origin main
fi
git -C "$SOURCE" checkout --detach "$E87N_SOURCE_REF"

echo "[1/7] Prepare E87N OpenWrt source"
cd "$SOURCE"
./scripts/feeds update -a
./scripts/feeds install -a
cp -f e87n.config .config
make defconfig

echo "[2/7] Build E87N kernel, DTB, rescue firmware and OpenWrt firmware"
make download -j"$JOBS"
make -j"$JOBS"

IMAGE_DIR="$SOURCE/bin/targets/mediatek/filogic"
SYSUPGRADE=$(find "$IMAGE_DIR" -maxdepth 1 -type f -name '*edgepi_e87n-squashfs-sysupgrade.bin' -print -quit)
INITRAMFS=$(find "$IMAGE_DIR" -maxdepth 1 -type f -name '*edgepi_e87n-initramfs-kernel.bin' -print -quit)
IMAGE=$(find "$SOURCE/build_dir" -type f -path '*/arch/arm64/boot/Image' -print -quit)
DTB=$(find "$SOURCE/build_dir" -type f -name 'mt7987a-edgepi-e87n.dtb' -print -quit)

test -s "$SYSUPGRADE"
test -s "$INITRAMFS"
test -s "$DTB"
test -s "$IMAGE"

rm -rf "$BUILD/debian-rootfs" "$BUILD/boot"
mkdir -p "$BUILD/debian-rootfs" "$BUILD/boot" "$OUTPUT/firmware"
cp -f "$SYSUPGRADE" "$OUTPUT/firmware/"
cp -f "$INITRAMFS" "$OUTPUT/firmware/"
cp -f "$IMAGE" "$BUILD/boot/Image"
cp -f "$DTB" "$BUILD/boot/mt7987a-edgepi-e87n.dtb"

echo "[3/7] Create Debian ARM64 rootfs"
if [ "$(dpkg --print-architecture)" = arm64 ]; then
    debootstrap --variant=minbase --arch=arm64 "$DEBIAN_RELEASE" \
        "$BUILD/debian-rootfs" "$DEBIAN_MIRROR"
else
    debootstrap --foreign --variant=minbase --arch=arm64 "$DEBIAN_RELEASE" \
        "$BUILD/debian-rootfs" "$DEBIAN_MIRROR"
    cp -f /usr/bin/qemu-aarch64-static "$BUILD/debian-rootfs/usr/bin/"
    chroot "$BUILD/debian-rootfs" /usr/bin/qemu-aarch64-static \
        /debootstrap/debootstrap --second-stage
fi

mount --bind /dev "$BUILD/debian-rootfs/dev"
mount -t proc proc "$BUILD/debian-rootfs/proc"
mount -t sysfs sysfs "$BUILD/debian-rootfs/sys"
trap 'umount -lf "$BUILD/debian-rootfs/sys" 2>/dev/null || true; umount -lf "$BUILD/debian-rootfs/proc" 2>/dev/null || true; umount -lf "$BUILD/debian-rootfs/dev" 2>/dev/null || true' EXIT

cp -L /etc/resolv.conf "$BUILD/debian-rootfs/etc/resolv.conf" 2>/dev/null || true
cat > "$BUILD/debian-rootfs/etc/apt/sources.list" <<EOF
deb $DEBIAN_MIRROR $DEBIAN_RELEASE main contrib non-free-firmware
deb $DEBIAN_MIRROR $DEBIAN_RELEASE-updates main contrib non-free-firmware
EOF

chroot "$BUILD/debian-rootfs" /bin/bash -c \
    'export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y systemd-sysv openssh-server sudo ca-certificates iproute2 iputils-ping net-tools vim-tiny curl; apt-get clean'

umount -lf "$BUILD/debian-rootfs/sys" 2>/dev/null || true
umount -lf "$BUILD/debian-rootfs/proc" 2>/dev/null || true
umount -lf "$BUILD/debian-rootfs/dev" 2>/dev/null || true
rm -f "$BUILD/debian-rootfs/usr/bin/qemu-aarch64-static"

echo 'e87n-debian' > "$BUILD/debian-rootfs/etc/hostname"
cat > "$BUILD/debian-rootfs/etc/fstab" <<'EOF'
PARTLABEL=rootfs / ext4 defaults,noatime 0 1
EOF

printf 'root:%s\n' "$ROOT_PASSWORD" | chroot "$BUILD/debian-rootfs" chpasswd
cat > "$BUILD/debian-rootfs/etc/ssh/sshd_config.d/10-e87n.conf" <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
EOF

mkdir -p "$BUILD/debian-rootfs/boot"
cp -f "$BUILD/boot/Image" "$BUILD/debian-rootfs/boot/Image"
cp -f "$BUILD/boot/mt7987a-edgepi-e87n.dtb" "$BUILD/debian-rootfs/boot/mt7987a-edgepi-e87n.dtb"

cat > "$BUILD/debian-rootfs/boot/boot.cmd" <<'EOF'
setenv kernel_addr_r 0x46000000
setenv fdt_addr_r 0x4a000000
setenv bootargs 'console=ttyS0,115200n8 root=PARTLABEL=rootfs rootfstype=ext4 rootwait rw'
load mmc 0:1 ${kernel_addr_r} /boot/Image
load mmc 0:1 ${fdt_addr_r} /boot/mt7987a-edgepi-e87n.dtb
booti ${kernel_addr_r} - ${fdt_addr_r}
EOF
mkimage -A arm64 -T script -C none -n 'E87N Debian boot script' \
    -d "$BUILD/debian-rootfs/boot/boot.cmd" "$BUILD/debian-rootfs/boot/boot.scr"

echo "[4/7] Build ext4 rootfs and boot filesystem"
ROOTFS_IMG="$OUTPUT/e87n-debian-rootfs.ext4"
BOOT_IMG="$OUTPUT/e87n-debian-boot.ext4"
rm -f "$ROOTFS_IMG" "$BOOT_IMG"
truncate -s "${ROOTFS_SIZE_MB}M" "$ROOTFS_IMG"
truncate -s 256M "$BOOT_IMG"
mke2fs -q -t ext4 -F -L rootfs -d "$BUILD/debian-rootfs" "$ROOTFS_IMG"
mke2fs -q -t ext4 -F -L boot -d "$BUILD/debian-rootfs/boot" "$BOOT_IMG"

echo "[5/7] Create boot bundle and checksums"
rm -rf "$OUTPUT/boot-bundle" "$OUTPUT/e87n-debian-bundle"
mkdir -p "$OUTPUT/boot-bundle" "$OUTPUT/e87n-debian-bundle"
cp -f "$BUILD/boot/Image" "$OUTPUT/boot-bundle/"
cp -f "$BUILD/boot/mt7987a-edgepi-e87n.dtb" "$OUTPUT/boot-bundle/"
cp -f "$INITRAMFS" "$OUTPUT/boot-bundle/e87n-initramfs-kernel.bin"
cp -f "$BUILD/debian-rootfs/boot/boot.scr" "$OUTPUT/boot-bundle/"
cp -f "$ROOT/README.md" "$OUTPUT/e87n-debian-bundle/README.md"
cp -a "$OUTPUT/boot-bundle" "$OUTPUT/e87n-debian-bundle/"
cp -f "$ROOTFS_IMG" "$OUTPUT/e87n-debian-bundle/"
cp -f "$BOOT_IMG" "$OUTPUT/e87n-debian-bundle/"
tar -C "$OUTPUT" -czf "$OUTPUT/e87n-debian-bundle.tar.gz" e87n-debian-bundle

if [ "$BUILD_RAW_IMAGE" = 1 ]; then
    echo "[6/7] Create GPT image without bootloader"
    RAW="$OUTPUT/e87n-debian-partitions.img"
    BOOT_RAW="$BUILD/boot.ext4"
    rm -f "$RAW" "$BOOT_RAW"
    truncate -s 256M "$BOOT_RAW"
    mke2fs -q -t ext4 -F -L boot -d "$BUILD/debian-rootfs/boot" "$BOOT_RAW"
    truncate -s 5120M "$RAW"
    sgdisk --zap-all "$RAW" >/dev/null
    sgdisk -n 1:2048:+256M -t 1:8300 -c 1:boot \
           -n 2:0:0 -t 2:8300 -c 2:rootfs "$RAW" >/dev/null
    dd if="$BOOT_RAW" of="$RAW" bs=1M seek=1 conv=notrunc status=none
    dd if="$ROOTFS_IMG" of="$RAW" bs=1M seek=257 conv=notrunc status=none
fi

echo "[7/7] Write manifest"
{
    printf 'source_ref=%s\n' "$E87N_SOURCE_REF"
    printf 'debian_release=%s\n' "$DEBIAN_RELEASE"
    printf 'kernel=%s\n' "$(basename "$IMAGE")"
    printf 'dtb=%s\n' "$(basename "$DTB")"
    sha256sum "$OUTPUT"/firmware/* "$OUTPUT"/*.ext4 "$OUTPUT"/*.img 2>/dev/null || true
} > "$OUTPUT/manifest.txt"

echo "Build complete: $OUTPUT"
