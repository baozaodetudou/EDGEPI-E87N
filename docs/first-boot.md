# E87N Debian Armbian 首启验证

本移植处于实验阶段，尚未上板验证。本文是首启操作的前置条件和验收方法，不是刷机脚本，也不表示已有可启动镜像。目标系统为 Debian Bookworm Armbian；参考仓库中的 OpenWrt 文件仅用于核对硬件和旧启动链。

## 1. 隔离 family 与设备树检查

以下框架证据基于本地 Armbian `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`。内核由独立 family 的 `KERNELBRANCH` 固定为 `commit:b864732ee285e7868fb0857d69a8ff349e37003e`（BPI-Router-Linux，Linux 6.12.108）；更新任一上游版本后需要重新核对。

- **独立 family：** 板卡使用 `BOARDFAMILY=edgepi-e87n`。`lib/functions/main/config-prepare.sh:141` 先据此设置 `LINUXFAMILY`，`lib/functions/configuration/main-config.sh:582–590` 再按这个名字选取存在的 family 文件，因此只加载 userpatches 中的 `config/sources/families/edgepi-e87n.conf`，避免进入上游 filogic 对其他 SoC 的分支。独立 family 内部再设 `LINUXFAMILY=filogic`，保持内核配置和包名兼容。更新上游时需重新验证文件选择与最终变量。
- **Board hook：** `lib/functions/general/extensions.sh:167–173` 识别双下划线 hook 名；`main-config.sh:362–369` 在 family 加载后注册并调用 `post_family_config`。此时板卡 hook 设置 `ATF_COMPILE=no`、`BOOTCONFIG=none`，跳过 bootloader 产物。
- **DTS bootargs：** [DTS 补丁](../userpatches/kernel/edgepi-e87n-6.12/0000-add-mt7987-e87n-dts.patch) 的 `/chosen/bootargs` 已改为 `console=ttyS0,115200n8`、`rootfstype=ext4`，并移除旧的 `root=PARTLABEL=rootfs` 和 squashfs/f2fs 参数。具体 root 设备由 Armbian 的启动配置提供；临时启动还应显式设置完整 bootargs，并核对生成 DTB 与最终 `/chosen/bootargs`。
- **内存：** 已补齐 `device_type = "memory"`，但容量仍保留参考 DTS 的 256 MiB 范围。实际板载 RAM 容量、保留区及 U-Boot 是否正确修正 DTB，需要从串口日志确认；不能把该默认值当成已测硬件规格。

配置加载成功、补丁无 fuzz 应用和 DTB 检查通过，均不代表内核编译或实机首启已经通过。

## 2. 确认实际 U-Boot 能力

仓库内没有与实机版本对应的 E87N 原厂 U-Boot 配置、命令日志或启动验证记录。相邻板卡的 U-Boot defconfig 不能作为 E87N 能力证明。

本地参考 `EDGEPI-E87N/target/linux/mediatek/image/Makefile:17–23` 为默认内核生成 LZMA FIT；`image/filogic.mk:1557–1579` 的 E87N 条目没有改成 extlinux 镜像；`filogic/base-files/lib/upgrade/platform.sh:165–179` 则向名为 `kernel`、`rootfs` 的分区升级。这些是旧镜像格式的证据，不能证明原 U-Boot 能直接加载裸 Image、解析 extlinux 或读取当前 ext4 特性。

在串口中记录下列只读信息。命令不存在也应记录，不要据此更换 bootloader：

```text
version
help booti
help bootm
help sysboot
help ext4load
help load
help part
bdinfo
mmc list
printenv bootcmd boot_targets kernel_addr_r ramdisk_addr_r fdt_addr_r fdt_high initrd_high
```

再根据 `help` 和实际介质枚举结果读取分区表。不要预设 U-Boot 的 mmc 编号与 Linux 的 `/dev/mmcblk0` 一致。

- extlinux 路径要求实际 U-Boot 能读取对应分区表、bootfs 文件系统、Image、DTB 和 initrd，并具有 extlinux 解析/启动能力；支持 `sysboot` 不等于 bootcmd 会自动扫描它。
- 手动 Image 路径要求 `booti` 及相应加载命令可用，还需核实可用 RAM、保留区和文件大小。
- 如果只确认了 FIT/`bootm` 路径，应另外生成与该 U-Boot 兼容的 Debian FIT 并验证。当前板卡配置没有生成这种 FIT；不能把 `.img`、OpenWrt sysupgrade 包或裸 Image 当作 FIT 使用。

## 3. 镜像分区不等于原厂布局

在 family 配置通过、没有额外分区扩展覆盖默认值的条件下，`OFFSET=16`、`BOOTSIZE=256`、`BOOTFS_TYPE=ext4`、`IMAGE_PARTITION_TABLE=gpt` 推导出：

| 区域 | 位置（512 字节扇区） | 内容 |
| --- | --- | --- |
| 磁盘开头 | LBA 0 起 | 新的保护 MBR、主 GPT 与分区项 |
| GPT 后至 16 MiB | bootfs 之前 | 空白区域；没有复制原设备启动数据 |
| 分区 1：bootfs | LBA 32768 起，256 MiB | ext4 启动文件系统 |
| 分区 2：rootfs | LBA 557056 起（272 MiB） | Debian ext4 根文件系统 |
| 镜像末尾 | 实际生成镜像的末端 | 备份 GPT |

依据是 Armbian `lib/functions/image/partitioning.sh:164–171` 的空镜像与偏移计算，以及 218、244 行生成的分区项。`lib/functions/image/rootfs-to-image.sh:99–103` 只在 `BOOTCONFIG!=none` 时注入 bootloader。跳过注入不会把空白区转换为“保留原盘数据”区域。

因此，将完整镜像从 eMMC 用户区 LBA 0 开始写入会替换主 GPT，并覆盖镜像范围内原有数据。前部空白同样可能覆盖 FIP、U-Boot 环境和 factory；具体重叠取决于实机原布局。eMMC 的 boot0/boot1 是独立硬件分区，不能因为它们未被普通用户区写入覆盖，就认定完整启动链仍然可用。

参考仓库 `image/filogic.mk:60–77` 的通用 MediaTek GPT 模板含 ubootenv/factory/fip 槽位，但 E87N 条目并未证明实机使用该模板。不得把通用模板的偏移当作 E87N 已测数据。上游 filogic 的 `write_uboot_platform()` 固定 BL2/FIP 偏移也不能作此证明；本工程的独立 family 不加载该写入函数。

首启前应把原始主/备 GPT、分区起止扇区、U-Boot 环境、factory 数据，以及实际承载 BL2/FIP 的用户区或 boot0/boot1 内容备份到独立存储，并确认恢复方法。不要把新的 bootfs/rootfs 简单平移或覆写到未知原分区。本工程尚未提供保留原厂启动链的 eMMC 安装方案。

## 4. 检查生成物和内核配置

必须检查实际生成的 Image、E87N DTB、initrd、内核模块和最终 `.config`；输入 defconfig 中省略某个符号不代表该符号被禁用，`olddefconfig` 会补入依赖和默认值。

板卡 `custom_kernel_config__edgepi_e87n_first_boot` 请求以下内建配置，并清除模块请求数组中可能存在的 `EXT4_FS`，避免 `opts_m` 在 `opts_y` 后应用时把它改回模块：

| 首启环节 | 最终 `.config` 应核实为 `y` |
| --- | --- |
| MT7987 设备树、时钟和引脚 | `ARCH_MEDIATEK`、`OF`、`COMMON_CLK_MT7987`、`PINCTRL_MT7987` |
| eMMC、块设备与 GPT | `REGULATOR_FIXED_VOLTAGE`、`MMC`、`MMC_MTK`、`MMC_BLOCK`、`PARTITION_ADVANCED`、`EFI_PARTITION` |
| 串口与早期日志 | `SERIAL_8250`、`SERIAL_8250_CONSOLE`、`SERIAL_8250_MT6577`、`SERIAL_OF_PLATFORM`、`SERIAL_EARLYCON` |
| 根文件系统与 initramfs | `EXT4_FS`、`EXT4_FS_POSIX_ACL`、`EXT4_FS_SECURITY`、`BLK_DEV_INITRD`、`RD_GZIP`、`RD_ZSTD`、`DEVTMPFS`、`DEVTMPFS_MOUNT` |
| PHY 固件加载 | `FW_LOADER` |

板卡还请求 `CONFIG_MEDIATEK_2P5G_PHY=m`，配合 rootfs 中的 MT7987 PHY 固件。该符号定义于固定 BPI 基线的 `drivers/net/phy/Kconfig:286`，`drivers/net/phy/Makefile:77` 将其映射到 `mtk-2p5ge.o`；752 补丁现已适配平铺的 `drivers/net/phy/mtk-2p5ge.c`。补丁的固件声明要求 `mediatek/mt7987/i2p5ge-phy-pmb.bin` 和 `mediatek/mt7987/i2p5ge-phy-DSPBitTb.bin`。安装固件与启用驱动都需要在最终 rootfs/内核产物中核对；本板卡 hook 不负责复制固件。

配置应用顺序的本地依据是 `lib/functions/compilation/kernel-config.sh:108–131` 和 `lib/functions/compilation/armbian-kernel.sh:745–781`。MT7987 时钟与 pinctrl 的默认启用条件分别在项目 `361-clk-...patch:32–36`、`360-pinctrl-...patch:20–25`；显式请求仍需通过最终 Kconfig 结果核实。

DTS 补丁 903–918 行的 MMC 节点使用 `mediatek,mt7986-mmc`，1394–1405 行启用 8 位、48 MHz、3.3 V、不可移除的 eMMC；678–691 行的 UART0 为 `0x11000000`、MT6577 兼容串口，1515–1518 行将其启用。不能仅因板卡是 MT7987 就推断需要一个不存在的 `MMC_MT7987` 或 `SERIAL_MT7987` 配置。

通用 Armbian GPT 没有 factory 分区。DTS 的两个 GMAC 已移除指向 factory 的 `nvmem-cells` / `nvmem-cell-names`，并在补丁 1348–1349、1356–1357 行留下临时随机 MAC 的说明，避免因不存在的 provider 持续返回 `-EPROBE_DEFER`。基线 `drivers/net/ethernet/mediatek/mtk_eth_soc.c:5047–5064` 的 `mtk_mac_assign_address()` 在地址查询返回 `-EPROBE_DEFER` 时直接延迟探测；在最终赋址阶段遇到其他查询错误时则调用 `eth_hw_addr_random()`。

这只是允许驱动使用临时随机 MAC 的首启措施，尚未保留原固定 MAC，也未实现地址持久化。重启或重新创建设备后地址可能变化，DHCP 租约或基于原 MAC 的绑定也可能不再匹配。DTS 中保留 factory 布局描述并不代表镜像保存了 factory 数据；恢复原固定地址仍需单独核对原数据、NVMEM 支持及持久化方案，网口行为仍待上板验证。

## 5. 临时启动 Debian

首启使用已确认可读的外部测试介质或网络加载路径，将启动文件加载到 RAM，并使用独立准备的 Debian 测试 rootfs。加载文件到 RAM 本身并不意味着启动后不会写磁盘；bootargs、fstab 和自动挂载设置必须指向测试介质，不能误选原 eMMC rootfs。

### extlinux 路径

Armbian 在 `lib/functions/rootfs/distro-agnostic.sh:196–216` 生成 kernel/initrd/fdt 项；独立 bootfs 中的路径相对该分区根目录，按当前 arm64 默认值应为：

```text
label Armbian
  kernel /Image
  initrd /uInitrd
  fdt /dtb/mediatek/mt7987a-edgepi-e87n.dtb
  append root=UUID=<Debian-rootfs-UUID> console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11000000 rootwait rootfstype=ext4 rw
```

这只是核对示例，必须以生成文件为准。Armbian 的 `partitioning.sh:380,522` 自动添加 rootfs UUID；板卡 `SRC_CMDLINE` 不再添加第二个 `root=`。若迁移 rootfs 改变了 UUID，应同时更新 extlinux 和 fstab。`PARTLABEL=rootfs` 在原 eMMC 与外部测试盘同时存在时可能不唯一；连 UUID 也必须检查是否因克隆而重复。

只有确认 U-Boot 的文件系统读取与 extlinux 路径有效后，才能临时调用该入口。不要修改或保存永久 bootcmd。

### 手动 booti 路径

1. 确认 `help booti` 成功，依据 `bdinfo`、环境与保留内存信息选择互不重叠的内核、DTB、initrd 地址；不要套用其他板卡地址。
2. 加载同一次构建的 `Image`、`mt7987a-edgepi-e87n.dtb` 与原始 `initrd.img-<内核版本>`。原始 initrd 加载完成后立即保存其字节长度，再加载其他文件，避免 U-Boot 的 `filesize` 被覆盖。
3. 显式设置包含唯一测试 rootfs 标识、`console=ttyS0,115200n8`、`earlycon=uart8250,mmio32,0x11000000`、`rootfstype=ext4 rootwait rw` 的完整 bootargs。核实最终 DTB `/chosen/bootargs` 不含 squashfs/f2fs 参数。
4. 对原始 initrd 使用 `booti <Image地址> <initrd地址>:<initrd字节长度> <DTB地址>`。这是参数结构，不是可直接复制执行的命令。不要将带 U-Boot 头的 `uInitrd` 当作原始 initrd 按这一路径传递；使用 uInitrd 时必须按实机支持的镜像处理方式验证。

即使 ext4 已改为内建，标准 Debian 启动仍保留 initrd，以处理 UUID 根设备解析等早期用户空间工作。不要把“只加载 Image 和 DTB”作为默认首启步骤。

## 6. 首启验收

保存完整串口日志，并在测试系统内核对：

- `/proc/cmdline` 只有一个 root 参数，根设备与 `/etc/fstab` 均指向预期测试 rootfs；`/etc/os-release` 确认为 Debian，Armbian 包与发行信息一致。
- MMC 枚举、GPT 识别、ext4 挂载和串口登录正常；没有持续 probe defer、时钟、引脚或 regulator 错误。
- 记录各网口本次使用的 MAC 和 DHCP 租约，核对随机地址回退与 PHY 固件加载后再逐项验证网口；不要把临时随机地址记为已恢复的原固定 MAC。随后验证 NVMe、USB、温度与风扇。
- 复核原设备分区表和启动链备份，确认测试未改变原盘；一次临时启动成功不等于 eMMC 安装方案或断电重启已验证。

本文不包含 eMMC 整盘刷写、GPT 重建、`saveenv`、boot0/boot1 切换或 bootloader 更新步骤。
