# E87N Debian / Armbian 构建仓库

这个仓库为 EdgePi E87N 构建 Debian ARM64 用户空间，并复用 E87N 专用的 MT7987 内核和设备树。它不替换原有 U-Boot，生成的 GPT 镜像也不包含 BL2、FIP 或 U-Boot。

## 产物

运行成功后，`output/` 会包含：

- `firmware/*sysupgrade.bin`：原仓库的 OpenWrt 回退固件；
- `firmware/*initramfs-kernel.bin`：通过 U-Boot 临时启动的救援内核；
- `boot-bundle/`：Debian 启动所需的 `Image`、E87N DTB、启动脚本；
- `e87n-debian-rootfs.ext4`：Debian ARM64 根文件系统；
- `e87n-debian-boot.ext4`：包含内核、DTB 和 `boot.scr` 的启动分区；
- `e87n-debian-partitions.img`：带 GPT 的 boot/rootfs 分区镜像，不包含启动加载器；
- `e87n-debian-bundle.tar.gz`：以上主要文件的打包文件。

## 构建

需要 Linux Docker 环境。Apple Silicon Mac 可以运行 Docker Desktop，但首次构建会下载并编译完整 OpenWrt，耗时较长。

```sh
cp config/build.env.example config/build.env
sed -i.bak 's/^ROOT_PASSWORD=.*/ROOT_PASSWORD=请改成你的密码/' config/build.env
JOBS=4 ./build.sh
```

`build.env` 默认锁定 E87N 源码提交 `c51dcd733aeda3c24c730ddaff2c54440a72ca6d`。如需跟随上游更新，修改 `E87N_SOURCE_REF` 后重新构建。

## 启动测试

先不要把 `e87n-debian-partitions.img` 直接写入设备。这个镜像只包含 GPT、boot 分区和 rootfs 分区，设备必须已有能读取 eMMC/GPT 的 U-Boot。

通过串口进入 U-Boot 后，先用 `printenv` 确认 `mmc` 编号和地址。启动脚本的核心命令是：

```text
setenv kernel_addr_r 0x46000000
setenv fdt_addr_r 0x4a000000
setenv bootargs 'console=ttyS0,115200n8 root=PARTLABEL=rootfs rootfstype=ext4 rootwait rw'
load mmc 0:1 ${kernel_addr_r} /boot/Image
load mmc 0:1 ${fdt_addr_r} /boot/mt7987a-edgepi-e87n.dtb
booti ${kernel_addr_r} - ${fdt_addr_r}
```

首次测试建议把 `boot.ext4` 和 `rootfs.ext4` 写到与设备 GPT 对应的分区，或者先把 rootfs 放在 USB/NVMe 上，再从 U-Boot 临时加载内核和 DTB。确认串口能启动、网口能用后，再考虑写入 eMMC。

## 风险边界

不要使用其他板卡的 Armbian 完整镜像，也不要把 `preloader.bin`、`bl31-uboot.fip` 或其他设备的 U-Boot 写入 E87N。刷写前先备份设备当前 GPT 和 U-Boot 环境；直接写错 eMMC 启动区会导致设备只能通过串口恢复。

