# 下载、校验与刷写边界

## 从哪里下载

公开用户产物只从仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 下载。
当前两个通道均标记为 Pre-release；使用前仍需按本页核对附件、SHA-256 和验收边界。
发布分为两个独立通道，每个 Release 只上传一个项目二进制附件：

| Release 通道 | 下载文件 | 使用场景 |
| --- | --- | --- |
| Image Release | `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar` | 新装或升级整个 Debian 系统 |
| Display Release | `e87n-display_<version>_all.deb` | 已运行 Debian 上单独升级小屏程序 |

Firmware 与 Display Release 不要求 tag、版本号或发布日期一致。固件内已经预装并经过 QEMU
验证构建时的同源 display 基线包；之后可以从更新的 Display Release 单独升级，不需要重新
构建或刷写固件。

不要从源码页下载 `Source code (zip)` / `Source code (tar.gz)` 当作系统镜像，也不要把
Actions 的中间 artifact 当作最终 Release 附件。

Image tag 为 `e87n-image-v<firmware_version>`，Display tag 为
`e87n-display-v<display_version>`。历史上带 Actions run ID 的 tag 以及同时包含 TAR/deb 的
Release 仅用于追溯；新 Release 每个通道只公开一个项目附件。

## 下载后先校验

下载 Firmware Release 后，Linux 使用：

```sh
sha256sum <固件文件>
```

macOS 使用：

```sh
shasum -a 256 <固件文件>
```

只升级显示包时，在对应 Display Release 中对 deb 独立执行：

```sh
# Linux
sha256sum e87n-display_<version>_all.deb
# macOS
shasum -a 256 e87n-display_<version>_all.deb
```

将结果与当前文件所属 Release 正文中的 SHA-256 比较。不要拿 Firmware Release 的摘要校验
Display 附件，反之亦然。`SHA256SUMS`、元数据和完整日志只保留在对应 Actions artifact，
不是 Release 的额外下载附件。

摘要不匹配、附件大小为 0、文件名与所属 Release 正文不一致时，停止操作。不要上传固件，
也不要安装未经对应 Display Release 校验的 deb。

## 哪个文件用于什么

| 文件 | 可以做什么 | 不可以做什么 |
| --- | --- | --- |
| `edgepi-e87n-debian_*_arm64-uboot-firmware.tar` | 原厂 U-Boot Web plain firmware 入口 | 不用于 LuCI、OpenWrt `sysupgrade`、SIMG/GPT/FIP 入口 |
| `e87n-display_*.deb` | 已启动 Debian 中安装/升级屏幕服务 | 不能单独启动设备，不能替代内核和 DTB |
| `.img` / `.img.xz` | 构建过程中的 GPT 中间产物 | 不是当前公开的 U-Boot 刷写附件 |
| Actions artifact | 构建日志、诊断和审计证据 | 不要从失败 run 中挑文件刷写 |

## U-Boot 刷写前检查

1. 设备可以进入原厂 U-Boot Web 页面，且网络连接稳定。
2. 电脑和设备在同一网段，浏览器能打开 U-Boot 地址。
3. 固件 TAR 的 SHA-256 已核对。
4. 已准备好断电、RESET 和重新进入 U-Boot 的恢复路径。
5. 已确认当前页面是原厂 U-Boot 的 `firmware` / plain firmware 上传入口。

固件 TAR 内部约定为：

```text
sysupgrade-edgepi-e87n/kernel
sysupgrade-edgepi-e87n/root
sysupgrade-edgepi-e87n/CONTROL
```

这里的 `sysupgrade` 只是兼容原厂 U-Boot Web 解析器的目录命名，不代表它是 OpenWrt
sysupgrade 包。它包含 Debian rootfs，不包含 OpenWrt 用户空间。

## 刷写后首次启动

如果你第一次操作 E87N，请先看[小白刷机与首次启动指南](QUICKSTART-BEGINNER.md)，其中有
断电、RESET、U-Boot 地址、浏览器上传、DHCP 查找和 SSH 登录的完整顺序。

刷写完成后等待设备启动，从 DHCP 租约中查找地址：

```sh
ssh root@<设备IP>
passwd
systemctl is-system-running
systemctl status ssh --no-pager
ip -br addr
apt update
```

显示和风扇检查：

```sh
e87nctl doctor
e87nctl fan status
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager
```

## 单独升级屏幕包

从 Display Release 下载并校验 `e87n-display_<version>_all.deb`，然后在已经启动的 Debian
系统上：

```sh
scp e87n-display_<version>_all.deb root@<设备IP>:/tmp/
ssh root@<设备IP>
apt install /tmp/e87n-display_<version>_all.deb
systemctl restart e87n-display.service
systemctl status e87n-display.service --no-pager
```

升级包不会替换内核、DTB、U-Boot、rootfs，也不会创建风扇控制守护进程。配置文件由
`dpkg` conffile 管理，管理员已有的 `/etc/e87n/display.json` 应按提示保留。

## 不能由下载证明的事情

SHA-256 只证明下载文件与 Release 附件一致。它不能证明：

- 设备一定能启动；
- 当前 U-Boot Web 页面一定接受该文件；
- 双网口 PHY、SPI 屏幕、背光和风扇在每台设备上都已完成实机验收；
- 设备中的数据已备份或刷写过程可恢复。

遇到启动失败时，优先重新进入 U-Boot，记录页面错误、设备状态和串口/网络日志，不要
连续重复刷写未知文件。
