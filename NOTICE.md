# 来源、归属与许可说明

本项目是非官方 E87N Debian/Armbian 移植，不声称得到 Armbian、Linux、MediaTek 或板卡厂商认证。
2026-09-13 的移植包括新内核系列适配、设备树修改、原生显示程序及构建/检查脚本。
本项目新增代码按根目录 [GPL version 2](LICENSE) 分发；已有第三方文件的具体许可与原作者声明保持不变。
该默认声明不覆盖下面单独许可的 PHY 微码，也不替代镜像中 Debian 包的各自许可证。

| 来源 | 使用内容 / 固定版本 | 归属与许可处理 |
| --- | --- | --- |
| [Armbian build](https://github.com/armbian/build/tree/7c1bb29eb0e7bd75b0703d86fe654b2680e646da) | rootfs、内核打包、配置接口；框架固定在该提交 | 按上游 GPL 声明；本仓库保留派生配置的 SPDX，并单独记录主机 PATH 修补 |
| [Linux stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=f6388029ea9e2c9e807d73827658738ea131faee) | 6.18.51 内核基线 | Linux 内核与各文件保留其原许可；本仓库提交补丁而非完整上游工作树 |
| [ZJJCKA/EDGEPI-E87N](https://github.com/ZJJCKA/EDGEPI-E87N/tree/c51dcd733aeda3c24c730ddaff2c54440a72ca6d) | 本地参考树版本：MT7987 硬件、DTS、驱动补丁 | 保留补丁内署名、SPDX 和作者信息；没有导入其 OpenWrt rootfs 或原显示 ELF |
| StarField Xu / NV3007 驱动 | 上述 E87N 源树的 `999990-add-nv3007-fbtft.patch`，适配为当前 `900-fb-nv3007-e87n.patch` | 保留驱动作者、GPL 声明及初始化序列；适配 Linux 6.18 Kconfig/Makefile |
| [linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware/-/commit/c0af6c70df291701fdecf6402e47dd4564e6b718) | 两份 MT7987 PHY 微码，版本 7.1 | 使用 [MediaTek 独立许可](firmware/LICENCE.mediatek)，不把微码改为 GPL；哈希和大小见 [firmware/README](firmware/README.md) |

参考树提交号用于追溯资料来源，不意味着所有旧补丁均未修改：6.18 系列包含本项目的 API 适配、
PHY 错误处理、温控及禁用未经验证 DVFS 等修改。当前补丁以 `userpatches/kernel/edgepi-e87n-6.18/` 为准；
6.12 目录仅作历史对照。

Debian 原生 `board-support/e87n/` 并非从原设备的预编译 musl ELF 反编译或直接复制而来。
Python、Pillow、DejaVu、systemd 等依赖在构建时由 Debian 软件包提供；其软件和许可证不作为本仓库原创代码声明。
原设备读取的程序及私有配置不在公开 Git 内容中，也不是可随意再分发的镜像附件。

本地镜像校验记录不等于公开二进制分发合规或安全审计已完成。未来分发镜像时，需要随实际附件
提供对应的源代码与构建材料、保留各包许可证，并独立处理默认账户与 SSH 密钥问题。
