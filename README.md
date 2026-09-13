# EdgePi E87N Armbian

为 **EdgePi E87N / MediaTek MT7987A / eMMC** 制作的实验性 Armbian 移植。目标为 **Debian 13 Trixie + Linux 6.18.51 LTS**，包含 NV3007 小屏和内核自动温控风扇支持。

**这是 Debian 系统，不是 OpenWrt 固件，也不是官方 Armbian 支持板卡。** OpenWrt/E87N 源码仅作为板级驱动、设备树和硬件接口参考；目标用户空间不包含 LuCI、UCI、procd 或原厂 musl 显示程序。

> 完整镜像构建、离线测试和真实镜像静态检查已通过，**尚未在 E87N 上完成启动或硬件验收**。不要通过 LuCI 上传本镜像，不要直接整盘覆盖原 eMMC。跳过 U-Boot 构建不等于保留原盘启动数据。

## 从这里开始

- [获取镜像与校验](docs/DOWNLOADS.md)：本仓库只提交源码和文档，镜像尚未发布为 GitHub Release 附件。
- [构建指南](docs/BUILDING.md)：Linux、macOS/Docker、Lima 说明及版本固定方式。
- [测试与验收](docs/TESTING.md)：区分代码夹具、镜像静态检查和实机测试。
- [屏幕与风扇使用](docs/display-fan.md)：亮度、页面、开关、配置及服务。
- [首启与刷写边界](docs/first-boot.md)：U-Boot 能力、备份、测试介质、账户与 SSH 风险。
- [2026-09-13 候选构建记录](docs/candidate-display-fan-20260913.md)：实际版本、SHA-256、验证结果及未完成项目。

## 功能与边界

| 功能 | 当前实现 | 实机状态 |
| --- | --- | --- |
| 系统 | Debian 13 最小命令行；已生成候选实际为 13.6 | 待首启 |
| 内核 | 固定 Linux 6.18.51，13 个 E87N 移植补丁 | 待首启 |
| 小屏 | NV3007、428×142 RGB565、四页状态界面 | 待验证颜色、方向及显示 |
| 背光 | 0–100% 亮度、开关、持久化；默认 20% | 待验证实际亮度和关闭 |
| 风扇 | 内核独占自动温控；温度、PWM 和冷却档位读取 | 待验证起转和散热 |
| 有线网络与存储 | MT7987 PHY 固件；MMC、USB-root、PCIe 等移植 | 网口/eMMC/USB/NVMe 待测 |

四个页面为 `overview`、`thermal`、`network`、`storage`，默认每 2 秒刷新。没有真实测速反馈就显示 `--`，不会把 PWM 百分比当作 RPM。

风扇使用四级 PWM `0/128/192/255`，50/65/75℃触发档位 1/2/3，迟滞 2℃。这些是软件策略，不是芯片额定温度或异常情况下的安全保证。显示服务不会写风扇节点，关屏不停止内核自动温控；当前不提供任意手动停扇或原 LuCI 网页。

以下限制仍然存在：CPU DVFS/CPU cooling 禁用；MT7987 WED 不支持；原固定 MAC 未恢复，可能使用不持久的随机地址；RAM fixup、原 U-Boot 加载能力及断电重启未经上板确认。完整编译不能证明全部驱动正常。

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

仅在新系统已经成功启动后使用：

```sh
e87nctl status
e87nctl fan status
sudo e87nctl display brightness 20
sudo e87nctl display screen thermal
sudo e87nctl display off
sudo e87nctl display on
```

配置位于 `/etc/e87n/display.json`，显示服务是 `e87n-display.service`。此镜像是 Debian 主机，不预设原厂 LAN/WAN、NAT、固定管理地址或路由防火墙策略。

**隔离首启并立即改密。** 当前候选沿用已记录的默认 `root/1234`、串口自动登录和初始共享 SSH host key 风险；先改密，确认主机密钥再生和唯一指纹后，再开放网络。不要以登录向导替代安全配置。详见 [首启指南](docs/first-boot.md)。

## 仓库结构

```text
build.sh / build-armbian.sh     Armbian 构建入口
build-lima.sh                   已有 Lima VM 的受限状态/构建/导出工具
board-support/                 Debian 原生小屏、背光 CLI、systemd 服务
userpatches/config/            板卡、family 和内核配置
userpatches/kernel/edgepi-e87n-6.18/  当前 13 个内核补丁
patches/armbian-build/          固定框架的主机兼容修补
firmware/                      有独立许可的 MT7987 PHY 微码
scripts/                       补丁、内核包、镜像和实机证据检查工具
tests/                         代码夹具和模拟回归测试
docs/                          构建、使用、验收及历史记录
```

`output/`、`source/`、镜像、内核包、构建日志、设备参考件和本地凭证不进入 Git。保留的 `edgepi-e87n-6.12/` 仅用于历史对照，不是当前默认配置。当前没有配置 GitHub Actions 自动构建或自动发布镜像。

## 来源、许可与反馈

本项目基于 [Armbian build](https://github.com/armbian/build)、[Linux stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/) 和 [ZJJCKA/EDGEPI-E87N](https://github.com/ZJJCKA/EDGEPI-E87N) 的相关板级资料。原作者及补丁声明予以保留；源码来源、版本与许可例外见 [NOTICE](NOTICE.md)。

本项目新增代码按 [GPL-2.0](LICENSE) 发布；第三方文件遵循各自已有声明，`firmware/` 内 MediaTek 微码**不属于 GPL 内核代码**，按其独立许可证分发。镜像内 Debian 软件包各自的许可证不由本仓库统一替换。

报告问题请提供本仓库提交号、镜像 SHA-256、串口启动日志和相关驱动错误，并先删除密码、密钥、内网 IP/MAC 及其他私人信息。不要把 `output/runtime/`、完整设备备份或私有日志直接提交到公开仓库。
