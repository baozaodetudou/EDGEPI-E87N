# 2026-09-13 原系统启动布局只读复核

在用户已提供访问方式的 E87N 上通过 SSH 执行只读查询；没有重启、刷写、修改账户、保存 U-Boot 环境或改变风扇/频率。本文省略 IP、口令、完整环境、MAC 和设备唯一标识。该结果属于 **原 OpenWrt 系统**，不是新 Armbian 的实机验收。

- 当前内核：`6.18.44`；运行时 DT model：`EdgePi E87N`。
- `MemTotal=1011132 kB`。
- 运行时 DT 的 memory/reg 原始十六进制为 `00000000 40000000 00000000 40000000`，即起始 `0x40000000`、大小 `0x40000000`（1 GiB）。这确认了这台设备原系统的内存描述与识别；没有验证新启动路径的 RAM/保留区交接。
- 原系统暴露频率列表 `500000 1300000 1600000 2000000`（kHz）。仅读取列表，不切换频率，不以此证明另一内核移植的供电/时钟驱动安全；本候选仍禁用未经验证的 CPU DVFS。

## eMMC 分区

起止由 sysfs `start`、`size` 和 `PARTNAME` 得到；单位为 512 字节扇区：

| 分区 | 名称 | 起始 LBA | 扇区数 | 起始偏移 |
| --- | --- | ---: | ---: | ---: |
| mmcblk0p1 | u-boot-env | 8192 | 1024 | 4 MiB |
| mmcblk0p2 | factory | 9216 | 8192 | 4.5 MiB |
| mmcblk0p3 | fip | 17408 | 4096 | 8.5 MiB |
| mmcblk0p4 | kernel | 21504 | 65536 | 10.5 MiB |
| mmcblk0p5 | rootfs | 87040 | 15181791 | 42.5 MiB |

另观察到独立的 `mmcblk0boot0`、`mmcblk0boot1`，各 4096 KiB。没有读取它们的内容，也没有生成完整备份或声称已有恢复介质。

当前 Armbian 整盘镜像在 16 MiB 才开始 bootfs，前部仍是新的 GPT 与空白。这会覆盖上表中的环境、factory、FIP 以及一部分原 kernel，**不是“跳过 U-Boot 编译便能保留原启动链”**。不能把新文件直接从 LBA 0 写到此 eMMC。

## U-Boot 可见证据与边界

仅对已确认的 2 MiB `fip` 分区进行只读字符串筛选，返回：

```text
booti
bootm
tftpboot
U-Boot 2025.07-Mediatek (May 01 2026 - 22:36:56 +0800)
```

字符串存在不证明这些命令已注册、当前运行的 bootloader 正是该副本、可读取特定文件系统，或默认 bootcmd 会加载 Armbian。没有匹配到 ext4/sysboot 字符串也不证明功能不存在（可能由布局、压缩或命令实现方式影响）。

`fw_printenv` 存在，但缺少 `/etc/fw_env.config` 且未找到 NVMEM provider，读取失败。没有猜测 offset、写入配置文件或调用 `fw_setenv`。因此尚无真实 bootcmd、加载地址、环境 CRC 或 RAM fixup 的确认记录。

下一步仍是准备可恢复的备份与串口，读取[首启指南](first-boot.md)列出的 U-Boot 能力、内存/保留区及启动地址，再选择外部测试介质临时启动。保留当前运行的 OpenWrt 不变。
