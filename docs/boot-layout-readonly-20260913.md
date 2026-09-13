# 2026-09-13 原系统启动布局只读复核

在用户已提供访问方式的 E87N 上通过 SSH 执行只读查询；没有重启、刷写、修改账户、保存 U-Boot 环境或改变风扇/频率。本文省略 IP、口令、完整环境、MAC 和设备唯一标识。该结果属于 **原 OpenWrt 系统**，不是新 Armbian 的实机验收。

- 当前内核：`6.18.44`；运行时 DT model：`EdgePi E87N`。
- `MemTotal=1011132 kB`。
- 运行时 DT 的 memory/reg 原始十六进制为 `00000000 40000000 00000000 40000000`，即起始 `0x40000000`、大小 `0x40000000`（1 GiB）。这确认了这台设备原系统的内存描述与识别；没有验证新启动路径的 RAM/保留区交接。
- 已读取的 RAM 保留区：wmcpu `0x50000000 + 0x100000`，ramoops `0x7ff70000 + 0x10000`，secmon `0x7ff80000 + 0x80000`。新 `902-e87n-memory-1g.patch` 据此设置板级 1 GiB 内存及顶部固件保留区，并保留 wmcpu；这不是新内核已在实机正确交接内存的证明。
- 原系统暴露频率列表 `500000 1300000 1600000 2000000`（kHz）。仅读取列表，不切换频率，不以此证明另一内核移植的供电/时钟驱动安全；本候选仍禁用未经验证的 CPU DVFS。
- 原系统的网口设备树身份已只读确认：eth0/of_node 对应 mac0，eth1/of_node 对应 mac1。此结果不是新 rootfs helper、DHCP 前 factory MAC 恢复或跨重启稳定性验证。

## eMMC 分区

整盘大小为 **15269888 扇区**。分区起止由 sysfs `start`、`size` 和 `PARTNAME` 得到；单位为 512 字节扇区：

| 分区 | 名称 | 起始 LBA | 扇区数 | 起始偏移 |
| --- | --- | ---: | ---: | ---: |
| mmcblk0p1 | u-boot-env | 8192 | 1024 | 4 MiB |
| mmcblk0p2 | factory | 9216 | 8192 | 4.5 MiB |
| mmcblk0p3 | fip | 17408 | 4096 | 8.5 MiB |
| mmcblk0p4 | kernel | 21504 | 65536 | 10.5 MiB |
| mmcblk0p5 | rootfs | 87040 | 15181791 | 42.5 MiB |

另观察到独立的 `mmcblk0boot0`、`mmcblk0boot1`，各 4096 KiB。没有读取它们的内容，也没有生成完整备份或声称已有恢复介质。

Armbian 两分区 GPT 中间镜像在 16 MiB 才开始 bootfs，前部仍是新的 GPT 与空白。这会覆盖上表中的环境、factory、FIP 以及一部分原 kernel。完整 `.img` / `.img.xz` 仅作为中间产物或历史证据，不能从 LBA 0 写到此 eMMC。

新交付格式改为[未压缩 USTAR 固件](UBOOT-FIRMWARE.md)：厂商参考 plain firmware（Web 类型 `fw`）路径先写 p5 rootfs，再写 p4 FIT kernel，并在 root payload 后擦除 512 KiB；只涉及 p5/p4，不写 SIMG/GPT/FIP/环境。它保留原分区布局，包内为 Debian ext4 rootfs。该行为来自匹配参考源码，不是实机刷写结果；本机尚未刷写。

## U-Boot 可见证据与边界

仅对已确认的 2 MiB `fip` 分区进行只读字符串筛选，返回：

```text
booti
bootm
tftpboot
sysupgrade
U-Boot 2025.07-Mediatek (May 01 2026 - 22:36:56 +0800)
```

FIP 内嵌 Web URL 为 `github.com/Yuzhii0718/bl-mt798x-dhcpd`，识别了源码家族。本地参考 checkout `4d5f0ffe02c5410c545bfb3f4112346877c75a72` 含与 E87N 匹配的板级 defconfig 和上述布局；实际二进制的提交未知。参考源码为 FIT 和 Web plain firmware 适配提供了依据，不能再描述为“没有对应板级配置或 FIT 生成支持”。字符串和源码匹配仍不证明串口 `help` 命令已实测、当前有效 bootcmd 或新 Debian FIT 能成功启动。

本次实际 FIP 字符串筛选找到 `sysupgrade`，未找到 `Firmware integrity verification failed`。这不证明精确构建配置，也不证明完整性检查被关闭；不能把未匹配到某条错误文本当作放行依据。

## 保存环境：离线 CRC32 已验证

早先 `fw_printenv` 因缺少 `/etc/fw_env.config` 而未能读取环境；后续直接只读取得 p1 内容，对 **`0x80000` 字节单环境**在主机离线验证 CRC32 成功。`0x80000` 是环境区域长度，不是 CRC 数值。本记录取代此前“尚无环境 CRC”的结论。

没有写 `/etc/fw_env.config`，没有调用 `fw_setenv` 或进行任何环境修改。只公开下列白名单；完整环境仍为私有资料：

| 字段 | 已保存内容 / 含义 |
| --- | --- |
| `bootmenu_0` | 启动项指向 `mtkboardboot` |
| `upgradefw` | 指向 `mtkupgradefw` |
| `httpd` | `Start Web failsafe` 入口 |
| `ipaddr` | `192.168.1.1` |
| `serverip` | `192.168.1.2` |
| `loadaddr` | `48000000`（十六进制 `0x48000000`） |

这些是读取结果，不是执行命令。**`bootcmd` 不在这份保存环境中**；不能推导它为空、不可用或已确认有效值。保存环境与编译默认环境、运行时变量需区分。上述地址属于 bootloader 环境，也不是 Debian 的固定管理 IP 或 Web 空闲 RAM 证明。

## 尚未完成的准备

没有重启板卡、刷写、完成全盘恢复备份或连接串口；环境副本 CRC 正确不等于恢复准备齐全。尚未做板上 RAM 测试、新内核内存交接或新固件硬件验收。`<=768 MiB` TAR 仅为静态打包政策，不是测得的 Web 可用 RAM。

下一步必须先完成板上 RAM 测试、独立存储上的恢复备份，确认串口或已经实测可恢复的 U-Boot Web/网络控制通道、实际 U-Boot 能力及物理恢复路径，满足用户“完全准备好再刷”的条件。优先评估网络 U-Boot，当前没有任何控制通道实测结果；USB Type-C 针脚/电平未知，不提供猜测接线。生产 FIT/initrd 按磁盘 root UUID 启动，诊断 RAM 测试必须另备仅驻留 RAM 或隔离的测试 rootfs。R4 本地打包/独立审计已 EXIT 0，完整 Linux regressions 已通过；这是历史 RAW 重新打包，不是新内核完整编译。新增编译检查再验证、主机导出复制仍待结果，未来工作流未 dispatch、远端发布未确认。详见[固件契约](UBOOT-FIRMWARE.md)与[首启指南](first-boot.md)。
