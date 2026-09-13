# EdgePi E87N Armbian

为 **EdgePi E87N / MediaTek MT7987A / eMMC** 制作的实验性 Armbian 移植。目标为 **Debian 13 Trixie + Linux 6.18.51 LTS**，包含 NV3007 小屏和内核自动温控风扇支持。

**这是 Debian 系统，不是 OpenWrt 固件，也不是官方 Armbian 支持板卡。** OpenWrt/E87N 源码仅作为板级驱动、设备树和硬件接口参考；目标用户空间不包含 LuCI、UCI、procd 或原厂 musl 显示程序。

> 当前[最小系统默认配置](docs/DEFAULTS.md)已通过 [Actions 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 的完整构建、真实镜像静态审计和产物上传，另有 VM 用户空间副本的 SSH/APT 集成结果，见[本轮记录](docs/ci-keygen-fix-20260913.md)。**尚未在 E87N 上完成启动或硬件验收**。不要通过 LuCI 上传，不要直接整盘覆盖原 eMMC。跳过 U-Boot 构建不等于保留原盘启动数据。

## 镜像与屏幕安装包下载

发布成功后，从 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 选择 tag，每个版本提供两个二进制附件：

- `Armbian-…_trixie_current_6.18.51_minimal.img.xz`：系统镜像，已预装屏幕控制程序。
- `e87n-display_<版本>_all.deb`：独立屏幕控制安装包，用于安装/升级；不需要重新刷镜像。

Release 正文直接列出下载链接和 SHA-256；GitHub 自动附带的 Source code zip/tar.gz **不是系统镜像**。

**首次发布仍须手动运行。** 已成功构建的 `34737922588` 不需要重编译：打开 [Publish existing E87N build](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/publish-e87n.yml) → **Run workflow** → 选择 `main`，`build_run_id` 填 `34737922588`，`release_tag` 留空即可。留空 run ID 则选择最近一次成功的 main 构建；仅在其 artifacts 尚未过期且全部校验通过时发布。新镜像使用 [E87N Debian 13 release](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml) 手动构建并发布。两个入口都不会因 push/tag push 自动触发。详细说明见[下载与校验](docs/DOWNLOADS.md)。

## 从这里开始

- [最小系统默认配置](docs/DEFAULTS.md)：`root` / `doumao`、SSH 22、DHCP、上海时区与中文 UTF-8；默认值以此为准。
- [系统范围与验证状态](docs/SYSTEM-READINESS.md)：最小配置、实际验证状态和硬件证据缺口。
- [只读诊断](docs/DIAGNOSTICS.md)与[网口稳定身份](docs/NETWORKING.md)。
- [获取镜像与校验](docs/DOWNLOADS.md)：tag Release 的两个下载附件、首次手动发布和 Actions 备用下载。
- [构建指南](docs/BUILDING.md)：Linux、macOS/Docker、Lima 说明及版本固定方式。
- [测试与验收](docs/TESTING.md)：区分代码夹具、镜像静态检查和实机测试。
- [屏幕与风扇使用](docs/display-fan.md)：亮度、页面、开关、配置及服务。
- [小屏独立软件包](docs/DISPLAY-PACKAGE.md)、[云端构建](docs/GITHUB-ACTIONS.md)与[可选存储模块](docs/OPTIONAL-STORAGE.md)。
- [首启与刷写边界](docs/first-boot.md)：U-Boot 能力、备份、测试介质、账户与 SSH 风险。
- [2026-09-13 历史屏幕/风扇候选记录](docs/candidate-display-fan-20260913.md)：仅对应当次旧配置的版本、SHA-256 与验证结果。

## 功能与边界

| 功能 | 当前实现 | 实机状态 |
| --- | --- | --- |
| 系统 | Debian 13 Trixie 最小命令行；`Asia/Shanghai`、`zh_CN.UTF-8`；正常使用 APT | 云端构建/静态审计通过；实机待首启 |
| 内核 | 固定 Linux 6.18.51，14 个 E87N 移植补丁 | 待首启 |
| 小屏 | 预装独立、版本化 `e87n-display` Debian 包；NV3007、428×142 RGB565、四页状态界面 | 待验证颜色、方向及显示 |
| 背光 | 0–100% 亮度、开关、持久化；默认 20% | 待验证实际亮度和关闭 |
| 风扇 | 内核独占自动温控；温度、PWM 和冷却档位读取 | 待验证起转和散热 |
| 有线网络与存储 | MT7987 PHY 固件；MMC、USB-root、PCIe 等移植 | 网口/eMMC/USB/NVMe 待测 |
| 额外存储 | `E87N_EXTRA_STORAGE=no`；DM/LUKS/LVM/RAID 等额外模块按需构建，管理工具按需安装 | 不自动部署数据盘；使用前单独验收 |
| 登录与诊断 | `root` / `doumao`，SSH 22 密码登录；首次 SSH 前生成独立 host keys；`e87nctl doctor` | 登录、密钥生成与完整启动待测 |

四个页面为 `overview`、`thermal`、`network`、`storage`，默认每 2 秒刷新。没有真实测速反馈就显示 `--`，不会把 PWM 百分比当作 RPM。

风扇使用四级 PWM `0/128/192/255`，50/65/75℃触发档位 1/2/3，迟滞 2℃。这些是软件策略，不是芯片额定温度或异常情况下的安全保证。显示服务不会写风扇节点，关屏不停止内核自动温控；当前不提供任意手动停扇或原 LuCI 网页。

以下限制仍然存在：CPU DVFS/CPU cooling 禁用；MT7987 WED 不支持；factory MAC 未恢复；GMAC 别名供 systemd 持久地址策略使用，但跨重启仍待测。DTS 默认内存仍为 256 MiB，原 OpenWrt 记录的这台设备为 1 GiB；新系统的 RAM fixup、原 U-Boot 加载能力及断电重启未经上板确认。完整编译不能证明全部驱动正常。

## 快速构建

在构建主机执行，不要在当前 OpenWrt 设备上运行：

```sh
git clone git@github.com:baozaodetudou/EDGEPI-E87N.git
cd EDGEPI-E87N
./build.sh
```

Linux 由 Armbian 执行构建；macOS 默认使用 Docker 的特权构建容器。需要网络、足够磁盘空间以及相应管理员/容器权限；详细前置条件和输出位置见 [构建指南](docs/BUILDING.md)。不要并发启动多个构建。

主要输出位于 `source/armbian-build/output/images/` 和 `source/armbian-build/output/debs/`。重新构建后必须重新验收；旧的同名压缩包不能作为新构建的完成证据。

| 构建输入 | 固定值 |
| --- | --- |
| Debian 目标 | `RELEASE=trixie` |
| Linux stable | `f6388029ea9e2c9e807d73827658738ea131faee`，6.18.51 |
| Armbian build | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| 板卡 / family | `edgepi-e87n` / `edgepi-e87n` |
| 内核配置 / 补丁集 | `linux-edgepi-e87n-lts` / `edgepi-e87n-6.18` |

Debian 包仍从签名软件源更新，容器工具链也不是完整快照，因此不承诺逐字节可重复构建。不会自动跟踪新内核；升级 LTS 需要重新移植和验收。7.2.5 不用于本镜像。为避免官方同名 Filogic 包覆盖板级移植，默认锁定内核、DTB、BSP 及重打包的 base-files，见 [构建指南](docs/BUILDING.md)。

## 登录新 Armbian 后

仅在按当前最小配置构建的新系统已经成功启动后使用；先从路由器 DHCP 租约或小屏读取实际 IP：

```sh
ssh root@<设备IP>
passwd
apt update
apt install --no-install-recommends curl
e87nctl doctor
e87nctl status
e87nctl fan status
e87nctl display config
e87nctl display brightness 20
e87nctl display screen thermal
e87nctl display refresh 2
e87nctl display off
e87nctl display on
```

密码为 `doumao`；它是公开默认值，首次仅接可信内网，登录后用 `passwd` 改密。没有首次创建用户向导或强制公钥门槛；串口需要正常认证。两个有线网口通过 networkd/netplan 请求 DHCP，没有固定管理地址或预设 LAN/WAN、NAT、路由防火墙策略。

显示配置位于 `/etc/e87n/display.json`，服务为 `e87n-display.service`；默认总览、20% 亮度、每 2 秒刷新。`display config` 只读查看配置，`display refresh` 接受整数 2–60 秒。小屏包可独立升级，见[软件包说明](docs/DISPLAY-PACKAGE.md)。最小系统不预装桌面、Web 后台、Docker、LuCI 或 RAID/LVM 管理套件。

**先确认文件所属配置。** 历史屏幕/风扇候选仍有旧默认口令、串口自动登录和构建时 SSH host keys 风险，详见[历史记录](docs/candidate-display-fan-20260913.md)；仓库更新不会修改这些旧文件。

## 仓库结构

```text
build.sh / build-armbian.sh     Armbian 构建入口
build-lima.sh                   已有 Lima VM 的受限状态/构建/导出工具
board-support/                 Debian 小屏、诊断、镜像默认配置与 systemd 服务
packaging/e87n-display/         独立版本化显示包与维护脚本
userpatches/config/            板卡、family 和内核配置
userpatches/kernel/edgepi-e87n-6.18/  当前 14 个内核补丁
patches/armbian-build/          固定框架的主机兼容修补
firmware/                      有独立许可的 MT7987 PHY 微码
scripts/                       补丁、内核包、镜像和实机证据检查工具
tests/                         代码夹具和模拟回归测试
docs/                          构建、使用、验收及历史记录
```

`output/`、`source/`、镜像、内核包、构建日志、设备参考件和本地凭证不进入 Git。保留的 `edgepi-e87n-6.12/` 仅用于历史对照，不是当前默认配置。镜像与独立显示包通过 tag Release 分发，原始内核包和日志仍可从源构建的 Actions artifacts 获取，具体名称见[下载说明](docs/DOWNLOADS.md)。两个发布入口**仅手动触发**；首次仍须手动运行，不会将已有 artifacts 自动转换为 Release。

## 来源、许可与反馈

本项目基于 [Armbian build](https://github.com/armbian/build)、[Linux stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/) 和 [ZJJCKA/EDGEPI-E87N](https://github.com/ZJJCKA/EDGEPI-E87N) 的相关板级资料。原作者及补丁声明予以保留；源码来源、版本与许可例外见 [NOTICE](NOTICE.md)。

本项目新增代码按 [GPL-2.0](LICENSE) 发布；第三方文件遵循各自已有声明，`firmware/` 内 MediaTek 微码**不属于 GPL 内核代码**，按其独立许可证分发。镜像内 Debian 软件包各自的许可证不由本仓库统一替换。

报告问题请提供本仓库提交号、镜像 SHA-256、串口启动日志和相关驱动错误，并先删除密码、密钥、内网 IP/MAC 及其他私人信息。不要把 `output/runtime/`、完整设备备份或私有日志直接提交到公开仓库。
