# 来源、归属与许可说明

本项目是非官方 E87N Debian/Armbian 移植，不声称得到 Armbian、Linux、MediaTek 或板卡厂商认证。
2026-09-20 起的维护路线使用 Frank-W `BPI-Router-Linux` 6.18 LTS 树；当前准确版本和
源码提交以 `userpatches/config/e87n-build.json` 为准。下文 6.18.51 的记录保留作历史来源。
2026-09-13 的移植包括新内核系列适配、设备树修改、原生显示程序及构建/检查脚本。
本项目新增代码按根目录 [GPL version 2](LICENSE) 分发；已有第三方文件的具体许可与原作者声明保持不变。
该默认声明不覆盖下面单独许可的 PHY 微码，也不替代镜像中 Debian 包的各自许可证。

| 来源 | 使用内容 / 固定版本 | 归属与许可处理 |
| --- | --- | --- |
| [Armbian build](https://github.com/armbian/build/tree/7c1bb29eb0e7bd75b0703d86fe654b2680e646da) | rootfs、内核打包、配置接口；框架固定在该提交 | 按上游 GPL 声明；本仓库保留派生配置的 SPDX，并单独记录主机 PATH 修补 |
| [Frank-W BPI-Router-Linux](https://github.com/frank-w/BPI-Router-Linux/tree/a638fabe36f293e58ab6be002af04b866959c546) | 当前 6.18.52 基线与维护中的 MT7987 SoC 驱动 | 保留 Linux、MediaTek、Frank-W 和其他贡献者的许可/署名；本板差异作为独立补丁维护 |
| [Linux stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=f6388029ea9e2c9e807d73827658738ea131faee) | 6.18.51 内核基线 | Linux 内核与各文件保留其原许可；本仓库提交补丁而非完整上游工作树 |
| [ZJJCKA/EDGEPI-E87N](https://github.com/ZJJCKA/EDGEPI-E87N/tree/c51dcd733aeda3c24c730ddaff2c54440a72ca6d) | 本地参考树版本：MT7987 硬件、DTS、驱动补丁 | 保留补丁内署名、SPDX 和作者信息；没有导入其 OpenWrt rootfs 或原显示 ELF |
| StarField Xu / NV3007 驱动 | 上述 E87N 源树的 `999990-add-nv3007-fbtft.patch`，适配为当前 `900-fb-nv3007-e87n.patch` | 保留驱动作者、GPL 声明及初始化序列；适配 Linux 6.18 Kconfig/Makefile |
| [linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware/-/commit/c0af6c70df291701fdecf6402e47dd4564e6b718) | 两份 MT7987 PHY 微码，版本 7.1 | 使用 [MediaTek 独立许可](firmware/LICENCE.mediatek)，不把微码改为 GPL；哈希和大小见 [firmware/README](firmware/README.md) |
| [Yuzhii0718/bl-mt798x-dhcpd](https://github.com/Yuzhii0718/bl-mt798x-dhcpd/tree/4d5f0ffe02c5410c545bfb3f4112346877c75a72) | 本地参考提交 `4d5f0ffe02c5410c545bfb3f4112346877c75a72`，E87N defconfig、eMMC 布局、plain firmware TAR/FIT 接口；`tests/vendor-untar/` 导入未修改的 `untar.c` / `untar.h` | Copyright (C) 2021 MediaTek Inc.，作者 Weijie Gao，保留 GPL-2.0/SPDX 和原作者声明；主机兼容头与 harness 单独提供 |

厂商解析器的固定源文件 SHA-256：

- `untar.c`：`2a4e02c9ab41e4e8c5910aaed581765477563d627754eb8cdd1600af74467c97`
- `untar.h`：`08783dc4981fd9eeb1fbd933828e5bfc7ca3ac303d74a9ba84fe18e3f7b656d9`

完整来源与测试边界见 [vendor-untar 说明](tests/vendor-untar/README.md)。原 FIP 的版本字符串为
`U-Boot 2025.07-Mediatek (May 01 2026 - 22:36:56 +0800)`，嵌入 Web URL 可识别上述源码家族；
参考提交包含匹配的 E87N defconfig/布局，但实际二进制提交未知，不声明已复现原厂 U-Boot。
新 [USTAR 固件](docs/UBOOT-FIRMWARE.md) 采用厂商解析器的目录约定，root payload 仍为 Debian ext4，
没有导入 OpenWrt rootfs，也不分发或更新设备 FIP、factory 数据或 U-Boot 环境。

参考树提交号用于追溯资料来源，不意味着所有旧补丁均未修改：6.18 系列包含本项目的 API 适配、
PHY 错误处理、温控及禁用未经验证 DVFS 等修改。当前补丁以 `userpatches/kernel/edgepi-e87n-6.18/` 为准；
6.12 目录仅作历史对照。

Debian 原生 `board-support/e87n/` 并非从原设备的预编译 musl ELF 反编译或直接复制而来。
Python、Pillow、DejaVu、systemd 等依赖在构建时由 Debian 软件包提供；其软件和许可证不作为本仓库原创代码声明。
原设备读取的程序及私有配置不在公开 Git 内容中，也不是可随意再分发的镜像附件。

本地镜像校验记录不等于公开二进制分发合规或安全审计已完成。未来分发镜像时，需要随实际附件
提供对应的源代码与构建材料、保留各包许可证，并独立处理默认账户与 SSH 密钥问题。
