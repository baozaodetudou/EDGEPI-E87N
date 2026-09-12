FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bc bison bzip2 ca-certificates cpio device-tree-compiler \
    debootstrap dosfstools e2fsprogs file flex gawk gdisk git \
    gzip libncurses-dev libssl-dev make openssh-client u-boot-tools \
    patch perl python3 python3-setuptools qemu-user-static rsync \
    subversion sudo swig tar time unzip wget xz-utils zstd \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
