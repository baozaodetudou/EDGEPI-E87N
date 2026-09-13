# E87N 最小系统范围与验证状态

目标是 **Debian 13 Trixie / Linux 6.18.51 最小命令行系统**，包含正常 APT、有线 DHCP、SSH 和小屏。默认配置以 [DEFAULTS.md](DEFAULTS.md) 为准；这仍是实验性 E87N 移植，尚无 Debian 实机首启与硬件验收记录。

当前最小配置已在 [Actions 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 完成完整构建、实际镜像静态审计和上传，源码为 `2a60011`，详见[故障修复及本轮产物记录](ci-keygen-fix-20260913.md)。旧的[屏幕/风扇候选](candidate-display-fan-20260913.md)不会因修改仓库而自动更新。可丢弃 rootfs 副本的用户空间集成与云端镜像构建是两类独立证据，均不包含实体板卡启动。

## 默认软件范围

| 能力 | 当前配置 | 验证边界 |
| --- | --- | --- |
| 登录 | `root` / `doumao`，SSH 22 允许 root 密码登录；串口正常认证；无创建用户向导或强制公钥门槛 | 公开默认密码，可信内网首启后运行 `passwd` 改密 |
| 设备身份 | 镜像清除 SSH host keys；首次 SSH 前生成独立密钥；空 machine-id 留待首启生成 | 副本上的密钥生成与持久性已测；完整首启服务时序仍待验证 |
| 网络与时间 | 两个有线网口使用 networkd/netplan DHCP；systemd-resolved / systemd-timesyncd；`Asia/Shanghai` | 无固定管理 IP、DHCP 服务器、LAN/WAN、网桥、NAT 或路由防火墙预设；实机 DHCP/DNS/NTP 待测 |
| 语言与软件管理 | `zh_CN.UTF-8`、`LANGUAGE=zh_CN:zh`；Debian 签名源，正常 `apt update` / `apt install` | UTF-8 与真实 APT 安装已在副本测试；包与 locale 不代表硬件验证 |
| 小屏与风扇 | 预装独立版本化 `e87n-display` 包；总览、20% 亮度、2 秒刷新；内核自动温控 | 包安装已在副本测试；实机显示、风扇起转与散热待测，没有第二个用户态风扇控制器 |
| 额外存储 | `E87N_EXTRA_STORAGE=no`；DM/LUKS/LVM/RAID 等额外内核模块显式选择构建；管理套件按需安装 | 不预装 RAID/LVM 管理套件，不创建阵列、加密卷或格式化磁盘，不提供加密/LVM 根启动承诺 |
| 诊断 | `e87nctl doctor` 检查身份、内存、根分区、网络、温控/屏幕注册和内核前提 | 始终报告 `hardware_validation=not-performed`，不会自动压力测试或写硬件 |
| 更新 | Debian 签名仓库；保留 Armbian 内核/DTB/BSP hold；手动安排升级 | 不自动升级、重启或跨 Debian 发行版；更换 E87N 内核仍要重新移植和验收 |
| Linux 基础 | 保留常规 Linux 与板级驱动、systemd、首启根分区扩容及既有容器内核基础 | 最终内核配置、模块与 rootfs 仍须按新构建产物检查 |

最小系统不预装 Docker、桌面、LuCI 或 Web 管理后台；需要的应用和维护工具通过 APT 按需安装。显示包可[独立升级](DISPLAY-PACKAGE.md)，额外存储模块见 [OPTIONAL-STORAGE.md](OPTIONAL-STORAGE.md)。

## 仍不能用软件离线验收代替的项目

1. **启动交接：** 原厂参考用 FIT，本工程用 Image/DTB/initrd/extlinux。必须确认实际 U-Boot 命令、加载地址与外部测试介质；目前没有实机启动成功记录。
2. **内存交接：** DTS 默认仍是 256 MiB；[原 OpenWrt 历史记录](openwrt-hardware-reference-20260913.md)的运行时 DT 和 `MemTotal=1011132 kB` 对应这台设备的 1 GiB RAM。新路径的 RAM/保留区和 U-Boot fixup 尚未核对，不能把原系统结果当成新镜像已识别全部内存。
3. **网口和存储：** 两个 2.5G 物理网口的连接、地址稳定性、吞吐；eMMC、USB3、NVMe 的 I/O、重启和断电恢复仍需测。网络身份说明见 [NETWORKING.md](NETWORKING.md)。
4. **屏幕与风扇：** NV3007 颜色/方向、背光、扇叶起转、温度精度和受控温升必须观察。读取 PWM 不是转速实测；不支持无依据停扇或高负载烤机。
5. **DVFS/CPU cooling：** 缺少经过确认的电源、OPP 和时钟切换证据，继续禁用；不恢复旧的猜测电压表。
6. **WED 硬件加速：** MT7987 的注册路径仍被移植补丁跳过，不能靠开关修好。普通软件网络与 WED 是两件事。
7. **启动链安装、RTC、复位与断电：** 原厂环境/factory/FIP 分区已有只读记录，但备份恢复流程与实际 U-Boot 命令能力仍需确认；未证明板载 RTC 存在，也未验证 watchdog/关机。没有整盘 eMMC 安装器。

因此不使用“完成 95%”之类无法核实的比例。软件构建完成、镜像静态检查通过和上板验收完成是三个不同状态。

[原系统布局历史只读记录](boot-layout-readonly-20260913.md)已确认环境/factory/FIP 的实际分区，以及原 FIP 中的 booti/bootm 字符串；仍未获得可用的环境读取和串口命令能力记录。

## 使用与维护

新系统成功启动后，使用 `root` / `doumao` 登录 SSH 22，先运行 `passwd`，再按需检查：

```sh
e87nctl doctor
e87nctl status
e87nctl fan status
systemctl status e87n-display systemd-networkd systemd-resolved systemd-timesyncd
lsblk -f
ip -br link
apt-mark showhold
```

不要原样公开可能包含 IP、MAC、UUID 的系统命令输出。doctor 已做固定字段过滤，但也不采集 SSH 身份或安全认证证据。

维护时先保存配置与已验证候选，再运行 `apt update`、检查 `apt list --upgradable`，确认后执行 `apt upgrade`；安装软件使用 `apt install`。不要盲目 `unhold` 内核包或把本板换成官方同名 Filogic 内核。默认时区为 `Asia/Shanghai`，locale 为 `zh_CN.UTF-8`；没有自动重启策略。

RAID/LVM 管理配置不属于默认最小系统；安装相关工具并选择额外内核模块后，应自行核对磁盘、备份、自动组装和卷激活策略。不能沿用旧配方曾预装工具或配置文件的说明作为当前默认行为。

## 已完成的用户空间集成检查

2026-09-13，在原生 ARM64 Debian 13 VM 中，对旧候选的可丢弃 rootfs 副本应用最新 customize hook，得到以下结果：

| 检查 | 已记录结果 |
| --- | --- |
| 系统验证器夹具 | 原 20 项，SSH keygen 修复后增至 27 项并全部通过；另有 5 项默认策略回归，包含 keygen mask、显示包版本、归属与 conffiles 检查 |
| 真实 rootfs 副本集成 | 最新 hook 安装版本化 `e87n-display` 包并设置 root 密码 `doumao`；实际副本的 `verify-system.py` 静态检查通过 |
| APT 与 locale | 签名源 `apt update`、安装并执行 `hello` 成功；`locale charmap` 为 `UTF-8` |
| SSH/PAM | 独立网络 namespace 内通过 loopback 完成真实 root 密码认证；测试监听端口为 22222，系统默认端口仍为 22 |
| SSH host keys | 新生成的两份身份互不相同，重复生成保留已有密钥 |
| 输入文件 | 测试前后原镜像内容未变 |

可重复入口为 `tests/smoke-minimal-userspace.sh`，前置条件和范围见 [TESTING.md](TESTING.md)。副本沿用旧内核与磁盘布局；这些结果不证明新最小内核、最终包集合、新完整镜像、PID 1 首启时序或实体 E87N 已通过，也不产生新镜像 SHA-256。

已复跑 DNS 顺序修正后的集成：定制结束后保留构建期 DNS，APT 更新及安装完成后才模拟框架最终 resolved 链接。密码 SSH、UTF-8、身份生成及原镜像不变检查再次通过。显示包安装/升级/卸载/重装/purge 的 19 项测试通过。

2026-09-13 11:10:49 CST 完成的 VM 镜像构建使用已被替代的旧配置，不交付为当前最小配置。随后首个最小配置云端镜像因 SSH keygen 检查器误报导致 job 失败；修复后，本轮 Actions 于 12:45:32 CST 完成，三个 job 均成功，完整镜像审计通过。失败产物的本地只读复验和可丢弃副本 SSH/APT 复测也通过，具体证据与边界见[本轮记录](ci-keygen-fix-20260913.md)。实机验收仍待进行，没有据此发布 Release。
