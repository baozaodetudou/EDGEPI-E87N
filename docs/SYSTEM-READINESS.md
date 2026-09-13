# E87N 最小系统范围与验证状态

目标是 **Debian 13 Trixie / Linux 6.18.51 最小命令行系统**，包含正常 APT、有线 DHCP、SSH 和小屏。默认配置以 [DEFAULTS.md](DEFAULTS.md) 为准；这仍是实验性 E87N 移植，尚无 Debian 实机首启与硬件验收记录。

当前交付为 [U-Boot 未压缩 USTAR 固件](UBOOT-FIRMWARE.md)，包含 FIT kernel、含 `/boot` 的 Debian ext4 root 和 CONTROL。**R4 本地已生成，独立最终审计 EXIT 0**；R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。主机导出及 SHA-256 比对已完成，V3 已废弃。完整 `.img` / `.img.xz` 仅为中间或历史文件，不可刷写。

旧最小配置曾在 [Actions 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 完成整盘镜像构建、静态审计和上传，源码 `2a60011`，见[历史记录](ci-keygen-fix-20260913.md)。旧[屏幕/风扇候选](candidate-display-fan-20260913.md)及其哈希不变；它们不证明新 TAR、902 或 factory 首启适配已完成。可丢弃 rootfs 副本集成与云端镜像构建也均不包含实体板卡启动。远端 Release 发布未确认。

## 本轮已报告的阶段结果

| 检查 | 当前证据与边界 |
| --- | --- |
| 原生内核 902 补丁 | `--dry-run --fuzz=0` 通过；不是完整新内核构建或上板结果 |
| factory 固件测试 | 加强后 24 项通过；不模拟 Web RAM、MMC 写入或实际 U-Boot 启动 |
| root adapter | 24 项通过；不证明 PID 1 首启、板上 p5 扩容和 factory MAC 恢复 |
| 首次实际 FIT 转换 | LZMA kernel 5993493 字节、原始 initrd 16328217 字节、DTB 21479 字节；FIT 可容纳于 32 MiB p4 |
| rootfs 重新整理 | 首次最小化结果约 820 MB，不适合上传上限；同 UUID 的新 735 MiB ext4 已完整复制，文件 SHA、属主、模式、硬链接和 xattrs 比较通过，不裁剪必要组件 |
| V3（已废弃） | TAR 793057280 字节曾通过当时静态审计；后续 Image 头复核发现内核地址须修正，V3 不能交付，不发布其哈希 |
| R4（本地生成/审计完成） | `Armbian-trixie-6.18.51-e87n-r4-uboot-firmware.tar`，793057280 字节；独立最终审计 EXIT 0，root 735 MiB / 770703360 字节 |
| 完整 Linux regressions | `ci-regressions.sh` 全部套件通过，`regressions-disk.log` EXIT 0；原传输与 tmpfs 故障日志保留 |
| 静态 CI | 85 项再次通过；新增 runtime/root preparer 两个编译检查目标也已复验通过 |
| 导出与发布 | 主机导出及 SHA-256 比对已完成；未来新源码完整构建工作流未 dispatch，远端 Release 未确认 |
| 实体设备 | 仅有原 OpenWrt 的只读证据；未重启、未刷写、未完成完整恢复备份、未连接串口，板上 RAM 测试未完成 |

R4 SHA-256：`b3587a5377edf7c95f0d620eb038e67643287ac75629f20cc1b071e5dfa47545`。源 RAW SHA-256：`289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9`，对应历史 Actions 34737922588；本轮未完整重编 Armbian/内核，DTB 内存/bootargs 包含 902 等效修正。实际 Image 头 `text_offset=0`、有效 `image_size=0x1690000`（23658496 字节）、`flags=0xa`；FIT load/entry `0x40000000`（2 MiB 对齐），DTB 为 1 GiB，已纳入 R4 独立审计。约束见 [Linux ARM64 booting](https://docs.kernel.org/arch/arm64/booting.html)与[固件记录](UBOOT-FIRMWARE.md)。第 4 次组装/复制比较成功后，首次审计遇 `/tmp` tmpfs ENOSPC；改用磁盘 `RUNNER_TEMP` / `/var/tmp` 对同一 TAR 复验全部通过，再以不覆盖已有文件方式暴露 R4。失败记录保留。用户要求完全准备好再刷：板上 RAM 测试、备份、串口或已经实测可恢复的 U-Boot Web/网络控制通道及物理恢复路径仍未完成，优先评估网络 U-Boot。

普通 TAR 安装和诊断 RAM 启动须区分：生产 FIT/initrd 按原 `.img` root UUID 寻找磁盘 rootfs，不是独立 RAM 测试系统。诊断需另备仅驻留 RAM 或隔离的测试 rootfs，核对挂载和扩容不触及原 eMMC；加载生产 FIT 到 RAM 或使用普通 Web firmware 上传不能替代该验证。

## 默认软件范围

| 能力 | 当前配置 | 验证边界 |
| --- | --- | --- |
| 登录 | `root` / `doumao`，SSH 22 允许 root 密码登录；串口正常认证；无创建用户向导或强制公钥门槛 | 公开默认密码，可信内网首启后运行 `passwd` 改密 |
| 设备身份 | 镜像清除 SSH host keys；首次 SSH 前生成独立密钥；空 machine-id 留待首启生成 | 副本上的密钥生成与持久性已测；完整首启服务时序仍待验证 |
| 网络与时间 | 两个有线网口使用 networkd/netplan DHCP；helper 在 DHCP 前只读 p2 `0x24`/`0x2a` 的 factory MAC；resolved/timesyncd；`Asia/Shanghai` | 原系统只读确认 eth0/eth1 的 of_node 为 mac0/mac1；新 helper 顺序、DHCP/DNS/NTP 与跨重启地址仍待测，无固定管理 IP 或 LAN/WAN/NAT 预设 |
| 语言与软件管理 | `zh_CN.UTF-8`、`LANGUAGE=zh_CN:zh`；Debian 签名源，正常 `apt update` / `apt install` | UTF-8 与真实 APT 安装已在副本测试；包与 locale 不代表硬件验证 |
| 小屏与风扇 | 预装独立版本化 `e87n-display` 包；总览、20% 亮度、2 秒刷新；内核自动温控 | 包安装已在副本测试；实机显示、风扇起转与散热待测，没有第二个用户态风扇控制器 |
| 额外存储 | `E87N_EXTRA_STORAGE=no`；DM/LUKS/LVM/RAID 等额外内核模块显式选择构建；管理套件按需安装 | 不预装 RAID/LVM 管理套件，不创建阵列、加密卷或格式化磁盘，不提供加密/LVM 根启动承诺 |
| 诊断 | `e87nctl doctor` 检查身份、内存、根分区、网络、温控/屏幕注册和内核前提 | 始终报告 `hardware_validation=not-performed`，不会自动压力测试或写硬件 |
| 更新 | Debian 签名仓库；保留内核/DTB/BSP hold；允许 apt update 和安装用户空间软件 | 不能解除内核 hold：单改 `/boot`/模块不会更新 p4 FIT；后续升级需成套重建 FIT/root/initrd/DTB/模块 |
| Linux 基础与扩容 | 通用 Armbian resize 禁用并 mask；严格校验 E87N 与完整原布局后只在 p5 内 resize2fs | 不改 GPT、不扩分区、不自动重启；最终产物与板上扩容/首启顺序仍待验收 |

最小系统不预装 Docker、桌面、LuCI 或 Web 管理后台；需要的应用和维护工具通过 APT 按需安装。显示包可[独立升级](DISPLAY-PACKAGE.md)，额外存储模块见 [OPTIONAL-STORAGE.md](OPTIONAL-STORAGE.md)。

## 仍不能用软件离线验收代替的项目

1. **启动交接：** 新转换器生成 FIT（LZMA 内核 + 原始 initrd + DTB），供原 p4 启动；extlinux 仅是旧/中间镜像路径。参考板级配置和原环境已有证据，但真实命令能力、当前有效 bootcmd 和新 Debian 启动仍待确认。
2. **内存与 Web 上传：** 902 已设置 `<0 0x40000000 0 0x40000000>` 的 1 GiB 范围，保留 wmcpu `0x50000000+0x100000`、ramoops `0x7ff70000+0x10000`、secmon `0x7ff80000+0x80000`。原系统 `MemTotal=1011132 kB` 不证明新路径交接或板上 RAM 测试通过；整个 TAR `<=768 MiB` 只是静态政策，不证明 Web 空闲 RAM 足够。
3. **网口和存储：** 两个 2.5G 物理网口的连接、地址稳定性、吞吐；eMMC、USB3、NVMe 的 I/O、重启和断电恢复仍需测。网络身份说明见 [NETWORKING.md](NETWORKING.md)。
4. **屏幕与风扇：** NV3007 颜色/方向、背光、扇叶起转、温度精度和受控温升必须观察。读取 PWM 不是转速实测；不支持无依据停扇或高负载烤机。
5. **DVFS/CPU cooling：** 缺少经过确认的电源、OPP 和时钟切换证据，继续禁用；不恢复旧的猜测电压表。
6. **WED 硬件加速：** MT7987 的注册路径仍被移植补丁跳过，不能靠开关修好。普通软件网络与 WED 是两件事。
7. **写入边界、恢复与断电：** 匹配参考 plain firmware（Web 类型 `fw`）先写 p5 rootfs、再写 p4 FIT，并在 root 后擦除 512 KiB；不写 GPT/环境/factory/FIP，不使用 SIMG。写入不是原子更新，备份恢复与实际 Web 行为仍待验证；没有整盘安装器。RTC、watchdog、关机和断电恢复也未验收。

因此不使用“完成 95%”之类无法核实的比例。软件构建完成、镜像静态检查通过和上板验收完成是三个不同状态。

[原系统只读记录](boot-layout-readonly-20260913.md)已确认 15269888 扇区 eMMC 的完整分区边界，并对 p1 的 `0x80000` 字节单环境离线验证 CRC32。没有写 `fw_env.config`、调用 `fw_setenv` 或修改环境；保存环境中无 bootcmd。FIP 版本/嵌入 Web URL 识别 `Yuzhii0718/bl-mt798x-dhcpd` 家族，参考提交 `4d5f0ffe02c5410c545bfb3f4112346877c75a72` 含匹配 E87N defconfig/布局，但实际二进制提交未知。FIP 中找到 `sysupgrade`、未找到 `Firmware integrity verification failed` 字符串均不能证明精确构建配置或关闭了完整性检查。串口命令能力仍未实测。

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

维护时先保存配置与已验证候选，可运行 `apt update`、检查 `apt list --upgradable`，在保留内核/DTB 等 hold 的前提下安排用户空间升级，安装软件使用 `apt install`。不要解除内核 hold 或安装官方同名 Filogic 内核；手动更新 initramfs 或 `/boot` 也不会同步 p4 FIT。默认时区为 `Asia/Shanghai`，locale 为 `zh_CN.UTF-8`；没有自动重启或 FIT 更新器。

RAID/LVM 管理配置不属于默认最小系统；安装相关工具并选择额外内核模块后，应自行核对磁盘、备份、自动组装和卷激活策略。不能沿用旧配方曾预装工具或配置文件的说明作为当前默认行为。

## 已完成的用户空间集成检查

以下是 TAR 格式切换前的历史结果。2026-09-13，在原生 ARM64 Debian 13 VM 中，对旧候选的可丢弃 rootfs 副本应用当时的 customize hook，得到以下结果：

| 检查 | 已记录结果 |
| --- | --- |
| 系统验证器夹具 | 原 20 项，SSH keygen 修复后增至 27 项并全部通过；另有 5 项默认策略回归，包含 keygen mask、显示包版本、归属与 conffiles 检查 |
| 真实 rootfs 副本集成 | 当时的 hook 安装版本化 `e87n-display` 包并设置 root 密码 `doumao`；实际副本的 `verify-system.py` 静态检查通过 |
| APT 与 locale | 签名源 `apt update`、安装并执行 `hello` 成功；`locale charmap` 为 `UTF-8` |
| SSH/PAM | 独立网络 namespace 内通过 loopback 完成真实 root 密码认证；测试监听端口为 22222，系统默认端口仍为 22 |
| SSH host keys | 新生成的两份身份互不相同，重复生成保留已有密钥 |
| 输入文件 | 测试前后原镜像内容未变 |

可重复入口为 `tests/smoke-minimal-userspace.sh`，前置条件和范围见 [TESTING.md](TESTING.md)。副本沿用旧内核与磁盘布局；这些结果不证明新最小内核、最终包集合、新完整镜像、PID 1 首启时序或实体 E87N 已通过，也不产生新镜像 SHA-256。

已复跑 DNS 顺序修正后的集成：定制结束后保留构建期 DNS，APT 更新及安装完成后才模拟框架最终 resolved 链接。密码 SSH、UTF-8、身份生成及原镜像不变检查再次通过。显示包安装/升级/卸载/重装/purge 的 19 项测试通过。

2026-09-13 11:10:49 CST 完成的 VM 镜像构建使用已被替代的旧配置，不交付为当前最小配置。随后首个最小配置云端镜像因 SSH keygen 检查器误报导致 job 失败；修复后，本轮 Actions 于 12:45:32 CST 完成，三个 job 均成功，完整镜像审计通过。失败产物的本地只读复验和可丢弃副本 SSH/APT 复测也通过，具体证据与边界见[本轮记录](ci-keygen-fix-20260913.md)。实机验收仍待进行，没有据此发布 Release。
