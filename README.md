# EdgePi E87N · Debian 13 / Armbian

面向 EdgePi E87N 的最小化 ARM64 Debian 系统工程。项目以 Debian 13 Trixie 为用户空间，
以 Linux 6.18 LTS 为内核基础，通过 Armbian 构建框架生成系统，并使用原厂 U-Boot 的
`firmware` 接口交付可刷写固件。

> 这是社区维护的 E87N 适配项目，不是官方 Armbian 板卡支持。刷写前请确认设备仍可进入
> 原厂 U-Boot Web 恢复页面，并先核对发布附件的 SHA-256。

## 你会得到什么

| 内容 | 默认行为 |
| --- | --- |
| 系统 | Debian 13 Trixie ARM64，systemd 管理服务 |
| 内核 | `6.18.52-current-edgepi-e87n`，配置与源码提交固定 |
| 登录 | `root` / `doumao`，SSH 允许密码登录；首次登录后立即修改密码 |
| 网络 | 双有线网口默认 DHCP，不预设 WAN/LAN、NAT 或固定 IP |
| 软件 | 可正常执行 `apt update`、`apt install`，保持命令行最小体积 |
| 本地化 | 时区 `Asia/Shanghai`，locale `zh_CN.UTF-8` |
| 风扇 | Linux 内核 `pwm-fan` / thermal governor 控制，用户空间只读取状态 |
| 小屏 | NV3007 framebuffer、背光和 `e87n-display.service`；显示包可独立升级 |

默认镜像不安装桌面、LuCI、Docker、DHCP 服务、NAT、RAID/LVM 管理套件或其他不必要的
后台组件。它是一个可通过 SSH 管理的基础 Debian 系统，不是 OpenWrt 发行版。

## 快速开始

### 获取产物

维护者在 GitHub Actions 中手动点击一次 **Run workflow**，流程会自动完成检查、编译、
镜像审计、U-Boot 固件转换、同产物 QEMU 验收，并发布一个新的 GitHub Pre-release。
不需要填写版本号、tag 或其他参数。

- [Actions 手动构建入口](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)
- [Releases 下载入口](https://github.com/baozaodetudou/EDGEPI-E87N/releases)
- [完整 Actions 说明](docs/GITHUB-ACTIONS.md)
- [下载、校验与刷写边界](docs/DOWNLOADS.md)

每个成功 Release 默认只有两个用户需要下载的附件：

1. `*-uboot-firmware.tar`：原厂 U-Boot Web 页面使用的 Debian 系统固件。
2. `e87n-display_<版本>_all.deb`：可在已经启动的 Debian 上独立安装/升级的屏幕控制包。

源码压缩包、GPT `.img`、`.img.xz`、Actions artifact 和内核调试包不是同一种交付物，
请按[下载说明](docs/DOWNLOADS.md)区分。

### 刷入系统

固件面向 E87N 原厂 U-Boot Web 恢复页的 `firmware` / plain firmware 入口。刷写前必须：

- 确认设备可以稳定进入 U-Boot Web 页面；
- 下载后执行 `sha256sum --check SHA256SUMS` 或按 Release 正文核对摘要；
- 保持设备有明确的断电和恢复路径；
- 不要把固件 TAR 上传到 LuCI、OpenWrt `sysupgrade`、SIMG、GPT 或 FIP 输入框。

详细流程见 [U-Boot 固件契约](docs/UBOOT-FIRMWARE.md) 和 [首次启动](docs/first-boot.md)。

### 首次登录

从路由器 DHCP 租约中找到设备地址，然后执行：

```sh
ssh root@<设备IP>
passwd
systemctl status ssh --no-pager
apt update
```

常用 E87N 检查命令：

```sh
e87nctl status
e87nctl doctor
e87nctl fan status
e87nctl display config
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager
```

## 屏幕与双网口界面

当前小屏的硬件链路已经按原始 E87N OpenWrt 项目适配：NV3007、428×142、RGB565、270°
旋转、PWM 背光和独立 Debian 服务。`e87n-display` 提供 overview、thermal、network、
storage 四个页面；显示程序不会接管风扇，只读取内核暴露的状态。

下一版界面按双网口设备重新设计：两个网口将分别显示 link、协商速率和 IP，右侧显示
CPU、内存、温度和风扇，布局以小屏可读性为第一优先级。设计规范和当前实现边界见
[屏幕设计与功能说明](docs/SCREEN-DESIGN.md)。

## 从源码构建

版本唯一来源是 [`userpatches/config/e87n-build.json`](userpatches/config/e87n-build.json)，
当前目标为 Debian 13 / Linux 6.18.52。构建入口：

```sh
python3 scripts/build_config.py
./build.sh
```

Linux 主机需要 Armbian 所需的 sudo、loop、挂载和镜像工具。macOS 建议使用 Docker；
完整依赖、缓存策略、产物位置和固件转换见 [构建指南](docs/BUILDING.md)。

## 项目结构

```text
board-support/                 E87N 用户空间、systemd、网络和工厂启动适配
packaging/e87n-display/        独立屏幕 Debian 包的元数据和维护脚本
userpatches/                   Armbian 配置、内核配置和 E87N 内核补丁
scripts/                       构建、审计、固件转换、发布和校验工具
tests/                         单元、静态审计、包生命周期和 CI 契约测试
docs/                          构建、刷写、网络、屏幕、验证和发布文档
.github/workflows/             唯一的手动构建与 Release 工作流
```

## 重要边界

- 成功构建、静态审计和 QEMU 验收不等于所有 MT7987 外设都完成上板验收。
- QEMU 不模拟 E87N 的真实 SPI 屏幕、双网口 PHY、风扇转速和原厂 U-Boot 交接。
- 默认密码是公开的，只适合可信内网首次登录；请立即执行 `passwd`。
- 内核、DTB、initrd、模块和 FIT 必须成套构建，不能只替换 `/boot`。
- 不要使用未经 Release 校验、来源不明或历史记录中的旧 `.img.xz` 刷写设备。

## 文档导航

- [默认配置](docs/DEFAULTS.md)
- [GitHub Actions 手动构建与发布](docs/GITHUB-ACTIONS.md)
- [下载、校验与刷写](docs/DOWNLOADS.md)
- [构建指南](docs/BUILDING.md)
- [首次启动](docs/first-boot.md)
- [网络与双网口](docs/NETWORKING.md)
- [屏幕设计与功能](docs/SCREEN-DESIGN.md)
- [独立屏幕包](docs/DISPLAY-PACKAGE.md)
- [U-Boot 固件格式](docs/UBOOT-FIRMWARE.md)
- [测试与验收](docs/TESTING.md)
- [来源与许可](NOTICE.md)
