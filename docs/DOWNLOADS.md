# 下载、校验与刷写边界

## 从哪里下载

正式用户产物只从仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 下载。
每个成功 Release 是一个 Pre-release，包含：

```text
<版本>-uboot-firmware.tar
e87n-display_<版本>_all.deb
```

不要从源码页下载 `Source code (zip)` / `Source code (tar.gz)` 当作系统镜像，也不要把
Actions 的中间 artifact 当作最终 Release 附件。

## 下载后先校验

Linux：

```sh
sha256sum <固件文件>
sha256sum <e87n-display deb>
```

macOS：

```sh
shasum -a 256 <固件文件>
shasum -a 256 <e87n-display deb>
```

将结果与 Release 正文中的 SHA-256 比较。若 Release 附带 `SHA256SUMS`，也可以执行：

```sh
sha256sum --check SHA256SUMS
# macOS:
shasum -a 256 -c SHA256SUMS
```

摘要不匹配、附件大小为 0、文件名与 Release 正文不一致时，停止操作，不要上传 U-Boot。

## 哪个文件用于什么

| 文件 | 可以做什么 | 不可以做什么 |
| --- | --- | --- |
| `*-uboot-firmware.tar` | 原厂 U-Boot Web plain firmware 入口 | 不用于 LuCI、OpenWrt `sysupgrade`、SIMG/GPT/FIP 入口 |
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

在已经启动的 Debian 系统上：

```sh
scp e87n-display_<版本>_all.deb root@<设备IP>:/tmp/
ssh root@<设备IP>
apt install /tmp/e87n-display_<版本>_all.deb
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
