# E87N Debian Armbian 首启准备与验收

当前交付格式为 [U-Boot 未压缩 USTAR 固件](UBOOT-FIRMWARE.md)，目标为原厂 Web 恢复页 plain firmware（类型 `fw`）。包内 `kernel` 是 LZMA 内核、原始 initrd 和 E87N DTB 组成的 FIT；`root` 是含 `/boot` 的 Debian ext4。完整 `.img` / `.img.xz` 仅是构建中间产物或历史证据，不能直接刷写，也不能用 LuCI sysupgrade 安装新 TAR。

**用户要求完全准备好再刷。当前没有板卡重启、刷写、完整恢复备份、串口连接或板上 RAM 测试记录。** R4 本地已生成，独立最终审计 EXIT 0；factory 24 项、root adapter 24 项通过，完整 Linux `ci-regressions.sh` 已在磁盘临时目录运行并 EXIT 0。CI 85 项已再次通过；新增 runtime/root preparer 两个编译检查目标也已复验通过。主机导出与两文件摘要比对已完成；这不是硬件验收。R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。V3 已废弃，未来新源码工作流未 dispatch。这些结果不代替硬件和恢复准备，详见[阶段记录](SYSTEM-READINESS.md)。本文不提供刷写、修改环境或猜测 USB Type-C 针脚的步骤。

[DEFAULTS.md](DEFAULTS.md) 定义新系统的 `root` / `doumao`、SSH 22 密码登录、networkd/netplan DHCP、`Asia/Shanghai`、`zh_CN.UTF-8` 和正常 APT。当前配方预装显示包，启用 NV3007/背光节点、模块自动加载配置与显示服务；独立 deb 用于升级和重新安装。没有首次创建用户向导或强制公钥门槛，串口需要正常认证。配置启用不代表屏幕或风扇已经实机验收，candidate3 的旧 headless 验收也不能代替当前显示配置的验证。

## 1. 刷写前的准备条件

以下各项必须先完成，再评估实际刷写：

1. 将原 eMMC 用户区完整备份到独立存储，包含主/备 GPT、p1 环境、p2 factory、p3 FIP、现有 kernel/rootfs；另保存 boot0/boot1 等启动恢复所需内容。核对大小和摘要，并准备明确可执行的恢复方案。环境 CRC 校验或部分只读采集不能替代恢复备份。
2. 确认**串口或已经实测可恢复的 U-Boot Web/网络控制通道**，核实本机 U-Boot 版本、命令能力、恢复入口及物理恢复路径。用户优先选择网络 U-Boot；串口不是唯一选项。当前没有任何控制通道实测结果，USB Type-C 是否承载 UART 以及针脚/电平也未确认，不能依据其他板卡套用。
3. 完成板上 RAM 测试，核对 U-Boot 工作区、上传缓冲、FIT 加载与解压、initrd、DTB 和固件保留区。整个 TAR `<=768 MiB` 只是静态打包政策，不证明 Web 有足够空闲 RAM。
4. 完成本次构建和最终 TAR 审计，确认分区契约、payload 大小、厂商解析器兼容性、同套内核/模块/DTB/initrd、root UUID、首启扩容限制和 MAC helper。保存退出码、日志与 SHA-256，不能只凭文件存在判定通过。

当前这些条件未全部满足，原系统继续保留。一次构建成功也不会自动满足硬件与恢复条件。

### 正常 TAR 安装与诊断 RAM 启动是两条路径

TAR 是正常安装包，原厂 Web plain firmware 会写 p5/p4。其 FIT 内的原始 initrd 按原 `.img` 的 root UUID 挂载磁盘 rootfs，不能作为独立 RAM 测试系统；即便先把生产 FIT/initramfs 加载到 RAM，也不能假定启动后只使用 RAM。

刷写前的诊断必须另备仅驻留 RAM 的 rootfs 或隔离的测试 rootfs，验证 bootargs、fstab、自动挂载和扩容不会触及原 eMMC，并经上述已实测的控制通道执行。普通 Web firmware 上传不是无写入的诊断 RAM 启动入口。当前没有完成这一独立测试路径、控制通道或物理恢复路径的验证。

## 2. 已有只读证据及其限制

[2026-09-13 只读记录](boot-layout-readonly-20260913.md)确认运行时 model 为 `EdgePi E87N`，memory/reg 为 `<0 0x40000000 0 0x40000000>`（1 GiB），原系统 `MemTotal=1011132 kB`。保留区为 wmcpu `0x50000000+0x100000`、ramoops `0x7ff70000+0x10000`、secmon `0x7ff80000+0x80000`。这些是原 OpenWrt 的读取结果，不是新 Debian 的内存或稳定性验收。

eMMC 为 15269888 个 512 字节扇区，p1 env 起始/大小为 8192/1024，p2 factory 9216/8192，p3 FIP 17408/4096，p4 kernel 21504/65536，p5 rootfs 87040/15181791。厂商参考 plain firmware 路径先写 p5 root，再写 p4 FIT kernel，并在 root payload 后擦除 512 KiB；验收应包含该尾部范围。它不写 GPT、环境、factory、FIP 或 boot0/boot1，不能换用 SIMG/GPT/FIP 入口。此范围来自匹配参考源码，尚无实机刷写验证。

原 FIP 含 `U-Boot 2025.07-Mediatek (May 01 2026 - 22:36:56 +0800)` 和 Web URL `github.com/Yuzhii0718/bl-mt798x-dhcpd`。本地参考提交 `4d5f0ffe02c5410c545bfb3f4112346877c75a72` 含匹配的 E87N defconfig/布局，实际二进制提交仍未知。新转换器已有 FIT 生成实现；这与“实机已经验证 bootm/FIT 启动”是不同结论。

p1 的 `0x80000` 字节单环境只读副本已离线通过 CRC32 校验；未写 `/etc/fw_env.config`，未调用 `fw_setenv`，未修改或保存环境。公开白名单记录启动菜单指向 `mtkboardboot`、升级项指向 `mtkupgradefw`、`httpd` 为 Start Web failsafe，保存的 `ipaddr=192.168.1.1`、`serverip=192.168.1.2`、`loadaddr=48000000`。`bootcmd` 不在保存环境中，当前有效值仍未确认；这些 bootloader 地址也不是 Debian 的固定管理地址。

## 3. 新 FIT、板级配置与根文件系统

版本与 pin 以 [e87n-build.json](../userpatches/config/e87n-build.json) 为准：当前为 Debian 13.7、Armbian `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` 和 Frank-W `a638fabe36f293e58ab6be002af04b866959c546`（Linux 6.18.52 LTS）。`BOARDFAMILY` 与 `LINUXFAMILY` 均为 `edgepi-e87n`，使用独立内核/DTB 包名及 `6.18.52-current-edgepi-e87n` release；board hook 设置 `ATF_COMPILE=no`、`BOOTCONFIG=none`，不构建或注入 bootloader。跳过注入不会使 Armbian 的新 GPT 自动保留原盘数据。

补丁集新增 `902-e87n-memory-1g.patch`，在 `901` GMAC aliases 之后把 E87N DT 描述改为实测的 1 GiB，并保留 wmcpu 和顶部 ramoops/secmon。应审计最终 FIT 内的 DTB 和 root 内 `/boot` 副本，再通过已验证的控制通道及启动证据核对实际交接；不再依赖“把历史 256 MiB 默认值交给 U-Boot 猜测修正”的说明。

必须对实际内核、DTB、initrd、模块、最终 `.config` 和 rootfs 检查以下约束，输入 defconfig 不代替最终配置：

| 首启环节 | 最终配置或产物要求 |
| --- | --- |
| 设备树、时钟和引脚 | `ARCH_MEDIATEK`、`OF`、`COMMON_CLK_MT7987`、`PINCTRL_MT7987` 内建 |
| eMMC、GPT 和根文件系统 | `MMC`、`MMC_MTK`、`MMC_BLOCK`、`EFI_PARTITION`、`EXT4_FS` 内建，相关 regulator/块设备依赖齐全 |
| 串口 | 8250、MT6577、OF console 与 earlycon 支持；板级 UART0 为 `0x11000000` |
| initramfs | `BLK_DEV_INITRD`、所用解压支持和 devtmpfs；原始 initrd 与 FIT 内核匹配，无通用 growroot/growpart/resize 路径 |
| PHY | `MEDIATEK_2P5GE_PHY=m`，`mtk-2p5ge.ko` 及所需 `mtk-phy-lib.ko`；两份 MT7987 PHY 固件齐全 |
| 屏幕和温控 | NV3007、背光、温度/PWM fan 配置及独立显示包一致；CPU DVFS/CPU cooling 禁用，MT7987 WED 仍不支持 |

新 rootfs 适配把 bootfs 内容放入根文件系统 `/boot`，移除原独立 `/boot` UUID 挂载。FIT bootargs 与 fstab 必须指向同一 root UUID，只有一个 `root=`，使用 ext4；包含 `net.ifnames=0`。新固件从 p4 FIT 启动，不消费历史中间镜像的 extlinux 入口。

离线打包在主机私有副本中重新整理 ext4：同 UUID 的新 735 MiB 文件系统完整复制目录树，文件 SHA、属主、模式、硬链接和 xattrs 比较已通过，不裁剪必要组件。R4 使用修正后的 Image 加载地址，独立最终审计已 EXIT 0，详见[固件契约](UBOOT-FIRMWARE.md)；这些主机检查与板上扩容或启动验收不同。

通用 Armbian resize 已由适配禁用并 mask，设置 `.no_rootfs_resize`。专用服务严格核对 E87N/eMMC 身份、所有原分区标签与起止、当前根设备及 ext4 几何后，**仅在既有 p5 内运行 resize2fs**；不改分区表、不重建 GPT、不自动重启。首启会写根文件系统，不能称为只读测试。root adapter 离线测试已报告通过，完整服务时序及板上扩容效果仍待验收。

factory MAC helper 在 DHCP 前只读 p2 的 `0x24` / `0x2a`，按 GMAC0/1 验证和应用地址，详见 [NETWORKING.md](NETWORKING.md)。读取不到合法 factory 地址时必须检查服务错误与实际回退，不能把获得 DHCP 租约当作 factory MAC 恢复成功。

## 4. 历史 GPT/extlinux 路径

旧 Armbian 中间镜像为两分区 GPT：bootfs 从 LBA 32768 开始、256 MiB，rootfs 从 LBA 557056 开始。历史 extlinux 使用独立 bootfs 中的 `/Image`、`/uInitrd`、`/dtb/mediatek/mt7987a-edgepi-e87n.dtb` 和 root UUID；旧文档也讨论过手动 booti 与外部 rootfs。它们仅用于理解历史产物，不是新 TAR 的安装或启动入口，本文不沿用其主动启动命令。

[9 月 13 日屏幕/风扇候选](candidate-display-fan-20260913.md)、[9 月 12 日 Trixie 候选](candidate-trixie-6.18.51-20260912.md)、[Bookworm 候选](candidate-20260912.md)和 [Actions 34737922588 记录](ci-keygen-fix-20260913.md)的版本、哈希、构建与审计证据原样保留。它们不证明当前 FIT/TAR、补丁 902 或首启 helper 已通过。2026-09-13 11:10:49 CST 的 VM 构建同样使用旧配置。

## 5. 准备齐全后的首启验收

首次网络验收只接一个网口到可信内网 DHCP 路由器，同时保留串口或已经实测可恢复的 U-Boot Web/网络控制通道，并确保物理恢复路径可用。从 DHCP 租约、小屏或可用控制台读取实际 IP；新系统无固定管理地址，也没有 OpenWrt LAN/WAN、网桥、NAT 或路由防火墙预设。SSH 默认 22，`root` 密码 `doumao`；首次登录执行 `passwd`，再按需要配置管理用户与公钥。

检查并保存以下结果，缺项应记为待验收：

- 从已验证控制通道取得的启动证据（可用时保存完整串口日志）、`/proc/cmdline`、Debian/Armbian 身份、实际内核版本；FIT/root/模块属于同一套输入，缺失日志应明确记录。
- 根设备为预期 p5，`/boot` 位于 root 内；GPT 与 p1/p2/p3 保留，专用扩容仅改变 p5 内的 ext4，服务失败有明确日志。
- 运行时 DT 显示 1 GiB 范围和三处固件保留区；核对 MemTotal、加载/解压无冲突和板上 RAM 测试记录。
- MAC 服务先于 DHCP，两个端口分别采用合法 factory 地址，确认 PHY 固件、链路、DHCP/DNS/NTP 及跨重启身份稳定。
- NV3007 颜色/方向、0/20/100% 亮度、开关和持久化；温度、内核风扇档位与实际起转。PWM 不等于实测 RPM，散热未确认前不做长时间压力测试。
- SSH host keys 与 machine-id 独立生成，重复启动保持身份；eMMC、USB/NVMe、重启与断电恢复分别验收。

历史可丢弃 VM rootfs 副本已记录 SSH/PAM、host keys、UTF-8 和 APT 集成结果，见 [SYSTEM-READINESS.md](SYSTEM-READINESS.md)；未启动目标内核或实体网口，不能证明上述服务时序。旧屏幕/风扇候选还有 `root/1234`、串口自动登录和初始 host keys 风险，必须按其历史记录处理。

新系统成功启动后允许 `apt update` 和 `apt install` 用户空间软件。内核、DTB 等板级包保持 hold，不能解除以独立升级内核：改动 `/boot` 或 initramfs 不会更新 p4 FIT，可能造成启动内核、initrd 与 root 内模块不同步。后续固件升级需成套重建并审计。

### 启动后收集证据

只有实际测试系统已启动，才使用 `scripts/collect-board-evidence.sh` 采集证据。脚本创建新的私有报告目录，读取身份、根设备、网口、PHY/温控/PWM 节点及有限日志；不会刷盘、配置网络或执行压力测试。缺权限、命令或节点会标记 `NOT-COLLECTED` 并返回非零，不能当作通过。报告可能含 IP/MAC 等私人信息，分享前须检查。

收集结束不代表硬件验收完成。本文不包含 eMMC 写入、GPT 重建、环境保存、boot0/boot1 切换或 bootloader 更新命令；远端 Release 发布仍未确认。
