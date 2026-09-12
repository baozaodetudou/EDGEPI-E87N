# EdgePi E87N Armbian

这是使用 Armbian 构建框架的 E87N Debian Bookworm 实验性移植，尚未完成上板首启验证。Armbian 负责 Debian rootfs、内核打包、ext4 分区和启动配置；MT7987 驱动与设备树参考 E87N 开源项目，目标系统不包含 OpenWrt 用户空间。

`BOOTCONFIG=none` 仅用于跳过 BL2/FIP/U-Boot 构建与镜像注入，并不保证整盘写入会保留设备原有 U-Boot。完整 `.img` 包含新的 GPT、空白区和文件系统；直接写入 eMMC 用户区可能覆盖原有 FIP、环境、factory 数据及分区表。

首启前必须阅读 [首启验证与写入边界](docs/first-boot.md)，核实实际 U-Boot 的加载能力、原分区布局以及 Image、DTB、initrd 和 rootfs 的对应关系。当前没有原厂 U-Boot 支持 extlinux 或 `booti` 的上板证据。

## 构建

需要 Linux 或 Docker 环境。第一次会拉取 Armbian 构建框架、Linux 6.12 内核和 Debian Bookworm 包。构建命令不代表移植已验证可启动；隔离 family 配置及首启检查见首启文档。

```sh
./build.sh
```

macOS 默认使用 Armbian 预构建容器；也可使用本机缓存的 Debian ARM64 基础镜像，让 Armbian 安装依赖：

```sh
E87N_BUILD_IMAGE=debian:13.6-slim ./build.sh
```

构建日志打印容器名并保存在 `source/armbian-build/output/logs/`；容器与 Linux 缓存卷会保留用于诊断。不要并发启动多个构建。当前进度和验证边界见 [构建状态](docs/build-status.md)。

Armbian 构建框架默认固定在 `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，避免后续上游变化影响首次移植。已有源码版本不一致时会停止，不会自动覆盖本地 checkout。macOS 启动器会在更换 overlay 之前检测已有的 E87N 构建容器并拒绝重复启动。

输出在：

```text
source/armbian-build/output/images/
```

成功构建后，磁盘镜像位于 `output/images/`，`linux-image-*.deb` 和 `linux-dtb-*.deb` 位于 `output/debs/`。这些目录相对于 `source/armbian-build/`。

首版为有线网络最小系统，只额外附带校验过的 MT7987 PHY 固件，不包含完整的通用 USB/Wi-Fi 固件集合。需要外接无线设备时，需按设备另行安装固件。

## 产物检查

生成包或提取镜像后，可执行只读静态检查；脚本不会挂载镜像、执行目标系统脚本或刷写设备：

```sh
# 只检查同一版本的内核与 DTB 包，不代表 rootfs 已通过验证：
bash scripts/verify-artifacts.sh --debs source/armbian-build/output/debs
# 分别提供已经提取的 Debian rootfs 与 bootfs：
bash scripts/verify-artifacts.sh --extracted-rootfs /path/to/rootfs --boot-dir /path/to/bootfs
```

验证包含内核/模块架构、首启配置、E87N DTB、Debian 标识、PHY 固件校验及 extlinux/fstab 的 root UUID 对应关系。不检查真实 U-Boot 能力、initrd 内容或整盘写入安全，不能代替上板测试。`bash tests/test-verify-artifacts.sh` 仅测试验证脚本自身，使用合成数据，不能作为真实镜像验证结果。

## 当前移植内容

`userpatches/config/boards/edgepi-e87n.csc` 定义 E87N 板卡和首启内核配置 hook；独立的 `userpatches/config/sources/families/edgepi-e87n.conf` 提供 MT7987 分支配置；`userpatches/kernel/edgepi-e87n-6.12/` 包含 MT7987 驱动补丁和 E87N DTB。板卡使用 `BOARDFAMILY=edgepi-e87n` 避免加载上游 filogic family，独立 family 内部保留 `LINUXFAMILY=filogic` 供内核配置与打包使用。

构建使用 `frank-w/BPI-Router-Linux`，内核固定为 `commit:b864732ee285e7868fb0857d69a8ff349e37003e`（Linux 6.12.108），然后应用 E87N 的 MT7987 补丁。它属于 Debian/Armbian 的内核构建过程，不使用 OpenWrt rootfs 或 OpenWrt sysupgrade 镜像。

DTS 已移除旧的 `root=PARTLABEL=rootfs` 和 squashfs/f2fs 参数，改用 115200n8 与 ext4；具体 root 设备由 Armbian 的启动配置提供。两个 GMAC 已移除对 factory MAC cells 的引用，以避免缺少 factory 分区时持续等待 NVMEM provider。当前允许驱动回退到临时随机 MAC，尚未保留原固定 MAC，也未实现地址持久化；重启后地址可能变化，不能依赖原 MAC 对应的 DHCP 绑定。

## 第一次启动

先取得串口日志，确认原 U-Boot 支持的命令和启动介质。extlinux 配置会引用 Image、initrd 和 E87N DTB；能否读取和启动仍取决于实际 U-Boot。Armbian 自动生成唯一的 root 参数，例如：

```text
root=UUID=<Debian-rootfs-UUID> console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11000000 rootfstype=ext4 rootwait rw
```

手动启动也需要明确处理 initrd、根设备和完整 bootargs。只有确认 `booti` 可用后才能采用 Image 启动路径；若仅支持 FIT/`bootm`，需另外准备包含 Debian 内核、DTB 和 initramfs 的兼容 FIT。具体步骤和证据要求见首启文档。

## 刷写边界

当前分区配置是通用 Armbian GPT：首分区偏移 16 MiB，256 MiB 的 ext4 bootfs，随后为 ext4 rootfs。这不是已核实的 E87N 原厂分区布局，也不是保留原启动链的安装器。首启应使用经确认的临时加载路径和独立测试 rootfs；完成原启动链备份、恢复方案及分区核对之前，不应向 eMMC 整盘写入完整镜像。
