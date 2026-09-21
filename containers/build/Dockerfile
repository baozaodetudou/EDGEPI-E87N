FROM debian:13.7-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
ENV DEBIAN_FRONTEND=noninteractive GIT_TERMINAL_PROMPT=0
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git sudo curl wget rsync xz-utils zstd procps file locales \
    build-essential bc bison flex libssl-dev libelf-dev dwarves pkg-config \
    python3 python3-yaml python3-pil fonts-dejavu-core fonts-wqy-microhei shellcheck jq \
    device-tree-compiler u-boot-tools cpio initramfs-tools-core \
    e2fsprogs util-linux fdisk gdisk parted dosfstools mtools \
    debootstrap cryptsetup-bin kmod qemu-system-arm qemu-utils \
    openssh-client sshpass expect dpkg-dev init-system-helpers \
    arch-test aptly aria2 binfmt-support btrfs-progs ccache dialog \
    fakeroot gawk gettext libncurses-dev lzop p7zip-full patch \
    pv python3-setuptools swig uuid-runtime whiptail zip unzip \
    && apt-get clean
WORKDIR /work/repo
CMD ["tail", "-f", "/dev/null"]
