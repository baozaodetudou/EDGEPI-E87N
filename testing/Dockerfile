FROM debian:13.7-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8

# Standalone image: no local builder base, KVM, loop devices or privileged mode.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates coreutils e2fsprogs openssh-client \
       python3 qemu-system-arm sshpass util-linux xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/e87n
COPY testing/ testing/
COPY scripts/factory_firmware.py scripts/build_config.py scripts/
COPY userpatches/config/ userpatches/config/
ENV PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python3", "/opt/e87n/testing/qemu_runner.py"]
