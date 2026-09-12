# EdgePi E87N Armbian

这是一个真正使用 Armbian 构建框架的 E87N Debian 镜像工程。Armbian 负责 Debian rootfs、内核打包、ext4 分区和启动配置；E87N 的 MT7987 内核补丁和设备树来自 E87N 开源项目，最终系统不包含 OpenWrt 用户空间。

当前构建保留设备原有 U-Boot，镜像不写入 BL2、FIP 或 U-Boot。这样可以先通过串口/U-Boot 验证 Debian，再决定是否调整 eMMC 分区。

## 构建

需要 Linux 或 Docker 环境。第一次会拉取 Armbian 构建框架、Linux 6.12 内核和 Debian Bookworm 包。

```sh
./build.sh
```

输出在：

```text
source/armbian-build/output/images/
```

重点文件通常包括 `Armbian_*.img.xz`、`linux-image-*.deb` 和 `linux-dtb-*.deb`。

## 当前移植内容

`userpatches/config/boards/edgepi-e87n.csc` 定义 E87N 板卡；`userpatches/config/sources/families/filogic.conf` 为 Armbian 的 MediaTek Filogic 家族增加 MT7987 和 E87N 内核分支；`userpatches/patch/kernel/edgepi-e87n-6.12/` 包含 MT7987 驱动补丁和 E87N DTB。

构建使用 `frank-w/BPI-Router-Linux` 的 `6.12-main` 作为 Armbian kernel source，然后应用 E87N 的 MT7987 补丁。它属于 Debian/Armbian 的内核构建过程，不使用 OpenWrt rootfs 或 OpenWrt sysupgrade 镜像。

## 第一次启动

先保留原 U-Boot，用串口观察启动。若设备的 U-Boot 支持 extlinux，启动分区中的配置会加载；启动参数应包含：

```text
console=ttyS0,115200n8 root=PARTLABEL=rootfs rootfstype=ext4 rootwait rw
```

如果 U-Boot 没有自动扫描 extlinux，可以在 U-Boot 中手动加载 `Image` 和 `mt7987a-edgepi-e87n.dtb`，然后使用 `booti` 启动。具体 `mmc` 编号和内存地址以设备的 `printenv` 输出为准。

## 刷写边界

Armbian 完整 `.img.xz` 是按 E87N GPT/boot 分区方案生成的，但默认不包含可替换的原厂 U-Boot。第一次不要直接覆盖 eMMC 启动区；先备份原 GPT、U-Boot 环境，并用 U-Boot 临时启动确认串口、eMMC、网口、NVMe 和 USB 正常。
