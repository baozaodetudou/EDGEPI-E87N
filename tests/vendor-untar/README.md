# Unmodified MediaTek TAR parser test fixture

`untar.c` / `untar.h`: GPL-2.0, Copyright (C) 2021 MediaTek Inc.
Source: [bl-mt798x-dhcpd, pinned revision](https://github.com/Yuzhii0718/bl-mt798x-dhcpd/tree/4d5f0ffe02c5410c545bfb3f4112346877c75a72/uboot-mtk-20250711/board/mediatek/common).

SHA256:

- `untar.c`: `2a4e02c9ab41e4e8c5910aaed581765477563d627754eb8cdd1600af74467c97`
- `untar.h`: `08783dc4981fd9eeb1fbd933828e5bfc7ca3ac303d74a9ba84fe18e3f7b656d9`

The compatibility headers supply host types/libc only; the parser body is not
modified. `main.c` opens a **regular file read-only**, invokes the actual parser,
and prints the two payload offsets/sizes for independent comparison. This does
not emulate MMC writes, Web upload buffering, U-Boot bootm, or board hardware.
The installed binary identifies this source family, but its exact commit is not
known; this reference revision is not claimed to be a reproducible binary match.
