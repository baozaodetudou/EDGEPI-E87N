# EdgePi E87N Armbian

为 **EdgePi E87N / MediaTek MT7987A / eMMC** 制作的实验性 Armbian 移植。目标为 **Debian 13 Trixie + Linux 6.18.51 LTS**，包含 NV3007 小屏和内核自动温控风扇支持。

**这是 Debian 系统，不是 OpenWrt 固件，也不是官方 Armbian 支持板卡。** OpenWrt/E87N 源码仅作为板级驱动、设备树和硬件接口参考；目标用户空间不包含 LuCI、UCI、procd 或原厂 musl 显示程序。

> 当前交付为原厂 U-Boot Web plain firmware（类型 `fw`）使用的[未压缩 USTAR 固件](docs/UBOOT-FIRMWARE.md)。**R4 已生成并独立审计 EXIT 0**，文件名、大小和 SHA-256 见[下载说明](docs/DOWNLOADS.md)。R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。新 735 MiB ext4 完整复制比较、factory 24 项、root adapter 24 项、完整 Linux regressions 和静态 CI 85 项通过；之后新增两个编译检查目标的再验证及主机导出复制仍待结果。**板卡尚未重启或刷写，RAM 测试、完整恢复备份、可恢复控制通道及硬件验收未完成，必须完全准备好再刷。**

## 固件与屏幕安装包下载

本次本地文件、SHA-256、最终审计与未完成的实机条件，见 [R4 生成与静态验收记录](docs/candidate-factory-r4-20260913.md)。

发布成功后，从 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 选择 tag，每个版本提供两个二进制附件：

- `<basename>-uboot-firmware.tar`：未压缩 USTAR，包含 `sysupgrade-edgepi-e87n/{kernel,root,CONTROL}`；kernel 为 LZMA 内核 + 原始 initrd + DTB 的 FIT，root 为含 `/boot` 的 Debian ext4，已预装屏幕控制程序。
- `e87n-display_<版本>_all.deb`：独立屏幕控制安装包，用于安装/升级；不需要重新刷镜像。

Release 正文直接列出两个附件的下载链接和 SHA-256；GitHub 自动附带的 Source code zip/tar.gz **不是系统固件**。完整 `.img` / `.img.xz` 只作中间产物或历史证据，不能刷写。新 TAR 使用厂商解析器的目录约定，不是 OpenWrt rootfs，不能用 LuCI sysupgrade 安装。

匹配的厂商参考 plain firmware 路径先写 p5 rootfs，再写 p4 FIT kernel，并在 root payload 后擦除 512 KiB；不写 SIMG/GPT/FIP/环境。`<=768 MiB` 整包上限只是静态打包政策，不能证明 Web 有足够空闲 RAM。完整契约与刷写前准备见 [UBOOT-FIRMWARE.md](docs/UBOOT-FIRMWARE.md)。

**唯一发布入口，无需填写参数：** 打开 [E87N Debian 13 release](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml) → **Run workflow** → 保持默认分支 `main` → 点击 **Run workflow**。每次都会重新构建本次运行选定的 main 提交，固定 Debian 13 Trixie / Linux 6.18.51；验证成功后并行构建镜像与显示包，再由 release job 自动生成并发布 `e87n-trixie-6.18.51-<GITHUB_RUN_ID>`。没有 tag、run ID 或内核输入框，也没有第二个发布工作流；自动生成 tag 只发生在手动运行内部，push、tag push 和定时任务均不触发。

**远端 Release 发布未确认，未来从新源码完整构建的手动工作流尚未 dispatch。** R4 是本地已生成并审计的 TAR，主机导出及 SHA-256 比对已完成。历史 `34737922588` artifacts 保留原始哈希和证据，不会自动转为 Release，详见[下载说明](docs/DOWNLOADS.md)。

## 从这里开始

- [最小系统默认配置](docs/DEFAULTS.md)：`root` / `doumao`、SSH 22、DHCP、上海时区与中文 UTF-8；默认值以此为准。
- [系统范围与验证状态](docs/SYSTEM-READINESS.md)：最小配置、实际验证状态和硬件证据缺口。
- [只读诊断](docs/DIAGNOSTICS.md)与[网口稳定身份](docs/NETWORKING.md)。
- [获取镜像与校验](docs/DOWNLOADS.md)：tag Release 的两个下载附件、无参数手动构建发布和历史 Actions 备用下载。
- [构建指南](docs/BUILDING.md)：Linux、macOS/Docker、Lima 说明及版本固定方式。
- [测试与验收](docs/TESTING.md)：区分代码夹具、镜像静态检查和实机测试。
- [屏幕与风扇使用](docs/display-fan.md)：亮度、页面、开关、配置及服务。
- [小屏独立软件包](docs/DISPLAY-PACKAGE.md)、[云端构建](docs/GITHUB-ACTIONS.md)与[可选存储模块](docs/OPTIONAL-STORAGE.md)。
- [U-Boot 固件契约](docs/UBOOT-FIRMWARE.md)、[原系统只读证据](docs/boot-layout-readonly-20260913.md)与[首启准备](docs/first-boot.md)：格式、独立 RAM 诊断、恢复备份、可恢复控制通道和验收条件。
- [2026-09-13 历史屏幕/风扇候选记录](docs/candidate-display-fan-20260913.md)：仅对应当次旧配置的版本、SHA-256 与验证结果。

## 功能与边界

| 功能 | 当前实现 | 实机状态 |
| --- | --- | --- |
| 系统 | Debian 13 Trixie 最小命令行；`Asia/Shanghai`、`zh_CN.UTF-8`；正常使用 APT | R4 本地打包/独立静态审计通过；硬件待首启 |
| 内核 | 源码固定 Linux 6.18.51、15 个补丁；R4 使用历史内核与 902 等效 DTB 修正 | 未完整重编新内核；实机待验收 |
| 小屏 | 预装独立、版本化 `e87n-display` Debian 包；NV3007、428×142 RGB565、四页状态界面 | 待验证颜色、方向及显示 |
| 背光 | 0–100% 亮度、开关、持久化；默认 20% | 待验证实际亮度和关闭 |
| 风扇 | 内核独占自动温控；温度、PWM 和冷却档位读取 | 待验证起转和散热 |
| 有线网络与存储 | MT7987 PHY 固件；DHCP 前只读 p2 factory MAC；严格布局校验后仅在 p5 内 resize2fs | root adapter 离线 24 项通过；完整首启及网口/eMMC/USB/NVMe 实机待测 |
| 额外存储 | `E87N_EXTRA_STORAGE=no`；DM/LUKS/LVM/RAID 等额外模块按需构建，管理工具按需安装 | 不自动部署数据盘；使用前单独验收 |
| 登录与诊断 | `root` / `doumao`，SSH 22 密码登录；首次 SSH 前生成独立 host keys；`e87nctl doctor` | 登录、密钥生成与完整启动待测 |

四个页面为 `overview`、`thermal`、`network`、`storage`，默认每 2 秒刷新。没有真实测速反馈就显示 `--`，不会把 PWM 百分比当作 RPM。

风扇使用四级 PWM `0/128/192/255`，50/65/75℃触发档位 1/2/3，迟滞 2℃。这些是软件策略，不是芯片额定温度或异常情况下的安全保证。显示服务不会写风扇节点，关屏不停止内核自动温控；当前不提供任意手动停扇或原 LuCI 网页。

CPU DVFS/CPU cooling 继续禁用，MT7987 WED 不支持。新 rootfs helper 在 DHCP 前只读 p2 的 `0x24`/`0x2a` 恢复 factory MAC；通用 Armbian resize 已由适配禁用，改为严格布局校验后只扩 p5 内 ext4。root adapter 离线 24 项通过，完整首启顺序及跨重启行为仍待验收。902 已按原系统只读 DT 修正为 1 GiB，并保留 wmcpu、ramoops、secmon，原生内核 dry-run 通过；新内核的实际内存交接仍待测。p1 的 `0x80000` 字节单环境已离线通过 CRC32 校验，未修改环境，保存环境中没有 bootcmd，详见[只读记录](docs/boot-layout-readonly-20260913.md)。

## 快速构建

在构建主机执行，不要在当前 OpenWrt 设备上运行：

```sh
git clone git@github.com:baozaodetudou/EDGEPI-E87N.git
cd EDGEPI-E87N
./build.sh
```

Linux 由 Armbian 执行构建；macOS 默认使用 Docker 的特权构建容器。需要网络、足够磁盘空间以及相应管理员/容器权限；详细前置条件和输出位置见 [构建指南](docs/BUILDING.md)。不要并发启动多个构建。

Armbian 中间镜像和包位于 `source/armbian-build/output/images/`、`source/armbian-build/output/debs/`。原始 `.img` 审计后由 `scripts/build-factory-firmware.py --image RAW --output <basename>-uboot-firmware.tar` 转换；这里 RAW 是主机上的普通 `.img` 文件。CI 的 TAR 输出位于 `output/ci/firmware/`。转换和最终固件审计见[构建指南](docs/BUILDING.md)，不能仅以旧同名压缩包或中间镜像生成成功判定交付完成。

| 构建输入 | 固定值 |
| --- | --- |
| Debian 目标 | `RELEASE=trixie` |
| Linux stable | `f6388029ea9e2c9e807d73827658738ea131faee`，6.18.51 |
| Armbian build | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| 板卡 / family | `edgepi-e87n` / `edgepi-e87n` |
| 内核配置 / 补丁集 | `linux-edgepi-e87n-lts` / `edgepi-e87n-6.18` |

Debian 包仍从签名软件源更新，容器工具链也不是完整快照，因此不承诺逐字节可重复构建。不会自动跟踪新内核；升级 LTS 需要重新移植和验收。7.2.5 不用于本固件。默认锁定内核、DTB、BSP 及重打包的 base-files；不要解除内核 hold，避免 `/boot`/模块更新后 p4 FIT 未更新而失配。允许 `apt update` 和安装用户空间软件；内核升级必须成套重建 FIT/root，见 [构建指南](docs/BUILDING.md)。

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
userpatches/kernel/edgepi-e87n-6.18/  当前 15 个内核补丁（含 902 内存修正）
patches/armbian-build/          固定框架的主机兼容修补
firmware/                      有独立许可的 MT7987 PHY 微码
scripts/                       补丁、内核包、镜像和实机证据检查工具
tests/                         代码夹具和模拟回归测试
docs/                          构建、使用、验收及历史记录
```

`output/`、`source/`、镜像、固件 TAR、内核包、构建日志、设备参考件和本地凭证不进入 Git。保留的 `edgepi-e87n-6.12/` 仅用于历史对照，不是当前默认配置。固件 TAR 与独立显示包通过 tag Release 分发，原始内核包和日志保留在对应构建的 Actions artifacts，具体名称见[下载说明](docs/DOWNLOADS.md)。唯一工作流**仅手动触发、无 inputs**，每次重新构建并自动生成 tag 发布。

## 来源、许可与反馈

本项目基于 [Armbian build](https://github.com/armbian/build)、[Linux stable](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/) 和 [ZJJCKA/EDGEPI-E87N](https://github.com/ZJJCKA/EDGEPI-E87N) 的相关板级资料。原作者及补丁声明予以保留；源码来源、版本与许可例外见 [NOTICE](NOTICE.md)。

本项目新增代码按 [GPL-2.0](LICENSE) 发布；第三方文件遵循各自已有声明，`firmware/` 内 MediaTek 微码**不属于 GPL 内核代码**，按其独立许可证分发。镜像内 Debian 软件包各自的许可证不由本仓库统一替换。

报告问题请提供本仓库提交号、固件 SHA-256、可获得的启动日志和相关驱动错误，并先删除密码、密钥、内网 IP/MAC 及其他私人信息。恢复通道可采用串口或已经实测可恢复的 U-Boot Web/网络控制通道，同时须确认物理恢复路径；当前均未实测。不要把 `output/runtime/`、完整设备备份或私有日志直接提交到公开仓库。
