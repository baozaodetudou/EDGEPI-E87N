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

发布分为两个互不阻塞的手动通道：

- **Firmware workflow / Release**：完成检查、镜像编译、U-Boot 固件转换与审计，并用
  固件构建时同源生成的基线 `e87n-display` 包执行 QEMU 验收；成功后只公开一个
  `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar`。
- **Display workflow / Release**：独立构建并验证显示包的安装、升级和卸载生命周期；成功后
  只公开一个 `e87n-display_<version>_all.deb`。

固件仍预装并验证构建时的 display 基线版本，但后续 display 可以按自己的版本和节奏高频
发布，无需重建或重新刷写固件。两个通道不要求使用相同 tag、版本号、run 或发布日期。

| 通道 | 版本来源 | Tag | 唯一项目附件 |
| --- | --- | --- | --- |
| Image | `e87n-build.json` 的 `firmware_version` | `e87n-image-v<firmware_version>` | `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar` |
| Display | `packaging/e87n-display/VERSION` | `e87n-display-v<display_version>` | `e87n-display_<display_version>_all.deb` |

正式 tag 不再包含 Actions run ID。同一版本只能发布一次；镜像内容变化必须递增
`firmware_version`，显示包内容变化必须递增 Debian 包版本。历史 run-id tag 和早期双附件
Release 保留用于追溯，不应作为当前命名模板。

- [Firmware workflow](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)
- [Display workflow](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-display.yml)
- [Releases 下载入口](https://github.com/baozaodetudou/EDGEPI-E87N/releases)
- [完整 Actions 说明](docs/GITHUB-ACTIONS.md)
- [下载、校验与刷写边界](docs/DOWNLOADS.md)

选择与任务对应的 Release：

1. 新装或升级整个系统时，进入 **Firmware Release**，只下载
   `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar`。
2. 已运行 Debian、只升级屏幕程序时，进入 **Display Release**，只下载
   `e87n-display_<version>_all.deb`。

源码压缩包、GPT `.img`、`.img.xz`、Actions artifact 和内核调试包不是同一种交付物，
请按[下载说明](docs/DOWNLOADS.md)区分。

### 刷入系统

固件面向 E87N 原厂 U-Boot Web 恢复页的 `firmware` / plain firmware 入口。刷写前必须：

- 确认设备可以稳定进入 U-Boot Web 页面；
- 下载后执行 `sha256sum <固件文件>`（macOS：`shasum -a 256 <固件文件>`），并按
  Firmware Release 正文核对摘要；
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

## 屏幕主题与页面轮换

当前小屏的硬件链路已经按原始 E87N OpenWrt 项目适配：NV3007、428×142、RGB565、270°
旋转、PWM 背光和独立 Debian 服务。`e87n-display` 提供 `overview`、`cpu`、`memory`、
`thermal`、`fan`、`network`、`traffic`、`storage` 八个页面；显示程序不会接管风扇，
只读取内核暴露的状态。

三种主题现在是相同布局的配色皮肤：`dark` 深色工业、`aurora` 黑紫霓虹、`light` 明亮高对比。
页面可以按 2–60 秒自动轮换，默认关闭；默认数据每 2 秒刷新，轮换间隔默认 3 秒。
没有风扇或 NVMe 遥测时，自动轮换会跳过对应可选页面。主题与轮换只改变显示方式，
风扇仍由 Linux 内核 thermal governor 控制。

```sh
e87nctl display theme dark
e87nctl display theme aurora
e87nctl display theme light
e87nctl display rotation on
e87nctl display rotation-seconds 3
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
systemctl restart e87n-display.service
```

屏幕包的独立安装、升级、配置修改、主题切换和真实设备验收见[独立屏幕包说明](docs/DISPLAY-PACKAGE.md)。
只修改主题或轮换配置不需要重新刷 U-Boot；在已经启动的 Debian 上升级对应的 `.deb` 即可。

面向第一次刷机的完整图文式步骤见[小白刷机与首启指南](docs/QUICKSTART-BEGINNER.md)，
设计规范、真实验收结果和当前实现边界见[屏幕设计与功能说明](docs/SCREEN-DESIGN.md)及
[真实板卡验收记录](docs/FINAL-VALIDATION-20260921.md)。

## 从源码构建

镜像构建输入和低频固件版本来自
[`userpatches/config/e87n-build.json`](userpatches/config/e87n-build.json)，当前
`firmware_version` 为 `2026.09.1`，目标为 Debian 13 / Linux 6.18.52。独立显示包版本来自
[`packaging/e87n-display/VERSION`](packaging/e87n-display/VERSION)。构建入口：

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
.github/workflows/             Firmware 与 Display 的独立手动构建/发布工作流
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
- [小白刷机与首次启动](docs/QUICKSTART-BEGINNER.md)
- [构建指南](docs/BUILDING.md)
- [首次启动](docs/first-boot.md)
- [网络与双网口](docs/NETWORKING.md)
- [屏幕设计与功能](docs/SCREEN-DESIGN.md)
- [独立屏幕包](docs/DISPLAY-PACKAGE.md)
- [U-Boot 固件格式](docs/UBOOT-FIRMWARE.md)
- [测试与验收](docs/TESTING.md)
- [来源与许可](NOTICE.md)
