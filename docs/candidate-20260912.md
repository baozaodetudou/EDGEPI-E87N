# E87N Debian/Armbian 实验镜像 — 2026-09-12

> 历史 Bookworm 候选，不是当前 Debian 13 屏幕/风扇版。新版见 [2026-09-13 候选记录](candidate-display-fan-20260913.md)；本地镜像不随 Git 克隆获取，见 [获取说明](DOWNLOADS.md)。

完整镜像已生成，并通过真实只读静态检查。**尚未上板启动，不能认定硬件已可用，也不要直接整盘刷入 eMMC。**

## 产物

- 系统：Debian 12 Bookworm，Armbian `26.11.0-trunk`，ARM64 最小命令行版。
- 内核：`6.12.108-current-filogic`，包含 E87N/MT7987 设备树及硬件移植补丁。
- 压缩镜像：`Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_bookworm_current_6.12.108_minimal.img.xz`，166,285,388 字节（约 159 MiB）。
- 解压后的 `.img`：1,124,073,472 字节（1072 MiB）。它是 GPT 磁盘镜像，不是 OpenWrt sysupgrade、factory 包或 U-Boot FIT。
- Mac 本地产物目录：仓库下 `output/releases/2026-09-12-e87n-bookworm/`。大文件未提交到 Git。

SHA-256：

```text
压缩 .img.xz：472558acf67a2675974b83b8f93f483c3d4beebd8f39e39516cb6b9cf0e2ffc9
解压 .img：  0d063d8946ad559fbc30ae5b41ff20baecf942c8c3df72d8c0aa9bd315ec659e
```

进入产物目录后可运行 `shasum -a 256 -c SHA256SUMS` 检查压缩文件。解压只生成普通文件，不等于刷写；不要把文件名当作设备路径。

## 已实际通过的检查

- 构建服务正常退出，内核链接、模块及 Debian 包完成；不是只生成配置或脚本。
- GPT、两个 ext4 文件系统的只读完整性检查通过。bootfs 从 16 MiB 开始、大小 256 MiB，rootfs 从 272 MiB 开始。
- 镜像内确认为 Debian Bookworm；Image、E87N DTB、内核配置及 AArch64 PHY 模块匹配。
- 两份 MT7987 PHY 固件和许可证校验通过。
- extlinux 只有一个 root 参数，并与实际根文件系统 UUID、fstab 匹配：`738f4a31-6e60-4080-a354-4e23d1efeef9`。
- 实际 initramfs 可解析，包含 `/init` 和 USB 根盘需要的九个模块/依赖。
- initramfs 内也包含 MT7987 PHY 模块和它需要的两份固件。
- 实际根文件系统中 systemd、Bash 为 AArch64，`ttyS0` 串口登录服务和 systemd-networkd 已启用，netplan 配置了有线接口 DHCP。这是文件检查，还不是串口登录或联网的实测结果。
- Mac 上的压缩文件 SHA-256、xz 完整性及解压数据流的 SHA-256 均与构建机结果一致。

这些是静态检查，不证明 U-Boot 能加载它，也不证明网口、eMMC、USB、NVMe、温度监测和重启已经在实机通过。

## 现在如何开始设备测试

先保留原固件和启动链。请通过串口进入**设备的 U-Boot 命令行**，记录完整启动日志，以及这些只读命令的输出；不存在的命令也照样记录：

```text
version
help booti
help bootm
help sysboot
help ext4load
help usb
bdinfo
mmc list
printenv bootcmd boot_targets kernel_addr_r ramdisk_addr_r fdt_addr_r fdt_high initrd_high
```

取得这些信息后，再决定 extlinux、手动 Image 或另外制作 FIT 的临时启动路径，并按实际内存布局选择加载地址。不要套用其他板卡地址，不执行 `saveenv`、分区重建或整盘写入。

首启优先使用独立外部测试介质，核对它的 UUID 与镜像启动配置一致、不与原盘重复。原 GPT、FIP/BL2、U-Boot 环境、factory 等数据仍需备份并确认恢复方法。`BOOTCONFIG=none` 不会让整盘写入自动保留原盘的启动数据。

首次网络测试只连接一个网口到 DHCP 路由器，并保留串口。这不是路由器 Web 管理系统，不预设原厂 LAN/WAN、固定管理 IP 或 NAT。当前网口可能使用临时随机 MAC；内存 DTS 暂保留参考值 256 MiB，必须核对实际 RAM 与 U-Boot 修正结果。

详细边界见 [首启验证](first-boot.md)；构建过程和证据见 [构建状态](build-status.md)。
