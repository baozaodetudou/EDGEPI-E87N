# E87N 原厂 U-Boot 固件格式与就绪条件

当前交付格式为 `<basename>-uboot-firmware.tar`：面向这台 E87N 原厂 U-Boot Web 恢复页 plain firmware（Web 类型 `fw`）的**未压缩 USTAR**。包内运行的是 Debian 13 Trixie / Armbian；`sysupgrade-` 目录名只是厂商解析器约定，不表示采用 OpenWrt rootfs，也不表示可用 LuCI sysupgrade 安装。

**R4 已生成，对同一 TAR 的独立最终审计 EXIT 0，随后以不覆盖已有文件的方式暴露最终文件。** R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。主机导出及 SHA-256 比对已完成，未来新源码完整构建的手动工作流未 dispatch，远端发布未确认。V3 因内核地址修正已废弃。板上 RAM 测试、备份、可恢复控制通道及硬件验收仍未完成；用户“完全准备好再刷”的条件尚未满足。

| 本轮阶段结果 | 已报告结果 |
| --- | --- |
| 原生内核 902 补丁 | `--dry-run --fuzz=0` 通过，不是完整编译/上板结果 |
| factory 固件测试 | 加强后 24 项通过，早期 19 项仅为阶段记录 |
| root adapter | 24 项通过，不包含实体首启 |
| 完整 Linux regressions | `ci-regressions.sh` 全部套件通过，`regressions-disk.log` EXIT 0 |
| CI 检查 | 85 项再次通过；新增 runtime/root preparer 两个编译检查目标也已复验通过 |
| 首次转换的 LZMA kernel | 5993493 字节 |
| 原始 initrd | 16328217 字节 |
| DTB | 21479 字节 |
| FIT 大小 | 首次生成的 FIT 可容纳于 32 MiB p4；大小合格不证明加载地址正确 |
| ext4 完整复制比较 | 同 UUID 的全新 735 MiB ext4；完整树的文件 SHA、属主、模式、硬链接和 xattrs 比较通过 |
| V3 / R4 | V3 的 793057280 字节 TAR 地址已废弃；R4 同为 793057280 字节，独立审计 EXIT 0，必须按 R4 SHA-256 区分 |

## R4 实际产物与来源

```text
文件名：Armbian-trixie-6.18.51-e87n-r4-uboot-firmware.tar
大小：793057280 字节（约 756.32 MiB）
SHA-256：b3587a5377edf7c95f0d620eb038e67643287ac75629f20cc1b071e5dfa47545
root：770703360 字节（735 MiB）
kernel FIT：22345728 字节
CONTROL：875 字节
独立最终审计：EXIT 0
```

源输入为历史 [Actions 34737922588](ci-keygen-fix-20260913.md) 的原始 RAW，SHA-256 为 `289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9`。R4 重新打包该 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。最终 FIT 为 22345728 字节，可容纳于 32 MiB p4；root 为 770703360 字节，CONTROL 为 875 字节。

第 4 次组装已生成 TAR，完整文件系统复制比较通过；首次审计因 Debian `/tmp` 的约 3.9 GB tmpfs 满而 ENOSPC。验证器改用磁盘上的 `RUNNER_TEMP` / `/var/tmp` 后，对**同一 TAR**独立重跑全部检查，退出 0，随后才以不覆盖已有文件的方式暴露 R4。原失败记录保留，不能把首次审计记为成功。

完整 Linux `ci-regressions.sh` 随后在磁盘临时目录通过全部套件，`regressions-disk.log` 记录 EXIT 0；此前输入传输缺少 docs、AppleDouble 元数据和 `/tmp` 满导致的失败已解决，失败日志仍保留。factory 24 项、root adapter 24 项通过，静态 CI 85 项再次通过；新增 runtime/root preparer 两个编译检查目标也已复验通过。

主机导出及 SHA-256 比对已完成，尚不声明导出/交付完成。未来手动工作流将从新源码完整构建，**尚未 dispatch**；本地 TAR 与审计完成不代表新 GitHub job、远端 Release 或板卡验收完成。

## 输入与容器

在专用 Linux 构建主机上，转换器接受已审计的、未压缩的 Armbian 两分区 GPT 普通文件：

```sh
sudo -n python3 scripts/build-factory-firmware.py \
  --image /path/to/Armbian-candidate.img \
  --output /path/to/candidate-uboot-firmware.tar
```

这里的路径均为构建主机文件，不能替换为物理块设备。转换器在私有副本中准备 rootfs，原 `.img` 保持不变。输入 bootfs 从 LBA 32768 开始、大小 524288 扇区，rootfs 从 LBA 557056 开始，扇区为 512 字节。**整盘 `.img` / `.img.xz` 仅是中间产物或历史证据，不是可刷写交付物**；其中的新 GPT 与空白前部会破坏原 eMMC 布局，不能从 LBA 0 覆盖原盘。

TAR 中的普通文件固定为：

```text
sysupgrade-edgepi-e87n/
  kernel     FIT：LZMA 压缩 ARM64 内核 + 原始 initrd + E87N DTB
  root       原始 ext4 Debian 根文件系统，/boot 位于其中
  CONTROL    格式、版本、源镜像摘要、payload 大小/摘要及待验收状态
```

外层 TAR 不使用 gzip/xz/zstd，也不使用 PAX/GNU 扩展。FIT 中使用原始 `initrd.img-<版本>`，不嵌套带 legacy U-Boot 头的 `uInitrd`；“原始”描述去掉 legacy 包装，不要求解开 initramfs 自身的压缩。`root` 是 ext4 文件系统镜像，不是文件目录归档、squashfs 或 OpenWrt overlay。`CONTROL` 是审计元数据，不是第三个刷写 payload。内核、DTB、initrd、模块与 `/boot` 必须对应同一套经审计输入。

### R4：依据实际 ARM64 Image 头修正地址

本轮实际 ARM64 Image 头为 `text_offset=0`、有效 `image_size=0x1690000`（23658496 字节）、`flags=0xa`。R4 使用 1 GiB DTB，FIT 内核 **load/entry 均为 `0x40000000`**，即 2 MiB 对齐的 RAM 基址；新源码显式检查对齐、Image 头及 image_size 范围。ARM64 启动位置与内核保留空间的要求见 [Linux ARM64 booting](https://docs.kernel.org/arch/arm64/booting.html)。

沿用旧厂商 OpenWrt 的 `0x40080000` 不满足这份 `text_offset=0` Image 的直接加载要求；若经 `booti_setup`，需重定位到 `0x40200000`，不能假定 FIT/bootm 路径会执行同样的重定位。V3 虽然大小及旧静态审计通过，仍因使用旧地址而废弃；不列出其交付哈希，也不把其结果转记为 R4 验收。

R4 独立最终审计已检查以下约束并 EXIT 0：拒绝 FIT `loadables`、非空 FIT reservation map、非法 ARM64 Image 头及额外 init 参数，要求 root ext4 使用 4 KiB 块，并检查 Image 对齐与有效大小边界。该结果只属于上述准确 R4 文件，不是板上启动或 RAM 测试。

## plain firmware 的写入边界

匹配的厂商参考源码中，plain firmware 类型 `fw` 解析 TAR 后，**先向 p5 `rootfs` 写入 `root`，再向 p4 `kernel` 写入 FIT `kernel`**。厂商路径还会在 root payload 末尾之后擦除 **512 KiB**；因此验收必须计入尾部擦除范围，不能只比较 root 文件大小与分区容量。这个过程不是原子更新，中途掉电仍可能留下不匹配的 kernel/root。

该路径仅涉及 p5 和 p4，不写 GPT、p1 环境、p2 factory、p3 FIP 或 boot0/boot1；不能替换为 SIMG、GPT、FIP 或 bootloader 更新入口。这是匹配参考源码的行为范围，尚未通过本机实际刷写验证。Web 字段属于原厂 U-Boot 恢复页，不是正在运行的 OpenWrt LuCI。

原盘大小为 **15269888 个 512 字节扇区**，已读到的布局如下：

| 分区 | 名称 | 起始 LBA | 扇区数 |
| --- | --- | ---: | ---: |
| p1 | u-boot-env | 8192 | 1024 |
| p2 | factory | 9216 | 8192 |
| p3 | fip | 17408 | 4096 |
| p4 | kernel | 21504 | 65536 |
| p5 | rootfs | 87040 | 15181791 |

FIT 必须容纳于 32 MiB 的 p4；root 和厂商尾部擦除必须落在既有 p5 内。分区编号、标签和边界必须全部匹配，不能据此推广到其他 E87N 容量或其他 MT7987 板卡。证据来源及限制见[只读布局记录](boot-layout-readonly-20260913.md)。

## 1 GiB 内存与上传限制

板级补丁 [`902-e87n-memory-1g.patch`](../userpatches/kernel/edgepi-e87n-6.18/902-e87n-memory-1g.patch) 根据原系统的运行时 DT，将 E87N 内存描述设为 `<0 0x40000000 0 0x40000000>`，即起始 `0x40000000`、容量 1 GiB，并保留以下固件区域：

| 区域 | 起始地址 | 大小 |
| --- | --- | --- |
| wmcpu | `0x50000000` | `0x100000` |
| ramoops | `0x7ff70000` | `0x10000` |
| secmon | `0x7ff80000` | `0x80000` |

保留 wmcpu，补充顶部 ramoops/secmon，其中 secmon 为 `no-map`。补丁选择单个 64 KiB ramoops record 是移植策略，不是原厂记录格式已获验证。旧的“DTS 仍为 256 MiB、没有 1 GiB 修正”描述仅适用于历史输入；新 DTB、U-Boot 交接和实际 RAM 稳定性仍需分别验收。

打包器对**整个未压缩 TAR**设置 `<=768 MiB` 的静态上限。它是打包政策，**不是 Web 可用空闲 RAM 的实测值或上传安全保证**。1 GiB 物理内存、保存环境中的 `loadaddr=48000000`、参考实现的上传缓冲位置也不能各自证明整包、LZMA 解压、initrd、FDT、U-Boot 工作区和固件保留区能安全共存。刷写前必须完成本机 RAM 测试，并核对当前 bootloader 的内存布局、真实包大小与加载/解压范围。

## rootfs 适配与维护

[`prepare-factory-rootfs.py`](../scripts/prepare-factory-rootfs.py) 将原独立 bootfs 内容放入根文件系统 `/boot`，移除旧的独立 `/boot` UUID 挂载，安装专用首启 helper 和服务。通用 Armbian resize 被禁用并 mask，同时设置 `.no_rootfs_resize`；initramfs 也必须排除通用 growroot/growpart/resize 路径。

专用扩容在校验 E87N 身份、eMMC、完整分区布局、当前根设备和 ext4 几何后，**仅在 `/dev/mmcblk0p5` 内执行 `resize2fs` 扩展文件系统**，不扩分区、不改 GPT、不自动重启。布局不符则拒绝，不提供任意目标盘参数。

离线打包改为**重新整理 ext4**：首次最小化结果仍约 820 MB，不适合上传上限；新实现创建相同 UUID 的全新 **735 MiB ext4**，以 `cp -a --preserve=all` 复制完整目录树，不裁剪必要组件。本轮完整树的文件 SHA、属主、模式、硬链接及 xattrs 比较已通过，R4 独立最终审计也已退出 0。操作仅涉及主机私有副本，与板上 p5 首启扩容不同，不证明实体首启或扩容通过。

MAC helper 在 DHCP 前只读 p2 factory 的 `0x24`、`0x2a` 偏移，各 6 字节，验证两个不同的有效单播地址并按 GMAC0/1 身份应用。它不写 factory 或 U-Boot 环境。原系统已只读确认 eth0/of_node 为 mac0、eth1/of_node 为 mac1；这不证明新 helper 已执行。root adapter 离线测试 24 项通过，服务顺序、失败回退和实机地址稳定性仍待验收，详见[网络说明](NETWORKING.md)。

内核与 DTB 等板级包保持 hold，避免 `/boot` 和模块更新后 p4 的 FIT 仍为旧版。普通 `apt update`、安装用户空间软件允许；不要解除内核 hold，也不要将独立内核安装或手动重建 initramfs 当作完整固件升级。没有自动 FIT 更新器；后续升级必须重新生成并审计成套 FIT、root、模块、DTB 和 initrd。

## 参考源码与已知证据

FIP 字符串为 `U-Boot 2025.07-Mediatek (May 01 2026 - 22:36:56 +0800)`；嵌入的 Web URL 指向 `github.com/Yuzhii0718/bl-mt798x-dhcpd`，可识别源码家族。本地参考 checkout `4d5f0ffe02c5410c545bfb3f4112346877c75a72` 包含与该板对应的 defconfig 和完全匹配的布局，**实际安装二进制的提交仍未知**，不声称二进制可复现匹配。实际 FIP 中找到 `sysupgrade`，未找到 `Firmware integrity verification failed`；字符串存在或未匹配均不能证明精确构建配置，也不能据此推导完整性检查已禁用。

[`tests/vendor-untar/`](../tests/vendor-untar/README.md) 保留该参考提交中未修改的 GPL-2.0 `untar.c` / `untar.h`、来源和 SHA-256，主机 harness 用于核对真实厂商解析器返回的 kernel/root 偏移与长度。加强后的 factory 测试 24 项通过，R4 TAR 已独立审计 EXIT 0；这些主机检查不模拟 MMC 写入、Web RAM 缓冲或 U-Boot 启动。许可归属见 [NOTICE](../NOTICE.md)。

p1 的 `0x80000` 字节单环境只读副本已离线验证 CRC32。未写 `/etc/fw_env.config`，未运行 `fw_setenv`，未修改或保存环境；保存环境中没有 `bootcmd`，不能推导当前有效 bootcmd。可公开白名单见[只读记录](boot-layout-readonly-20260913.md)。

## 刷写前必须完成

1. 记录本次冻结输入、原生构建退出状态、最终 TAR 大小及 SHA-256；完成 USTAR/厂商解析器、FIT/DTB、ext4/UUID、rootfs helper 和内核一致性审计。旧 `.img.xz` 的检查不能替代这些结果。
2. 确认**串口或已经实测可恢复的 U-Boot Web/网络控制通道**，核对本机 U-Boot 能力、恢复入口和加载范围，并确认物理恢复路径。优先按用户偏好的网络 U-Boot 路径评估，串口不是唯一选项；当前尚未实测任何控制通道。USB Type-C 是否承载串口、针脚与电平均未确认，不提供猜测接线。
3. 将完整 eMMC 用户区、主/备 GPT、p1 环境、p2 factory、p3 FIP 及 boot0/boot1 等恢复所需数据备份到独立存储，核对摘要并准备可执行的恢复方案。少量只读摘录和环境 CRC 校验不能替代完整恢复备份。
4. 完成独立的板上 RAM/诊断启动验证，再评估正常 TAR 安装。诊断 RAM 启动必须另备仅驻留 RAM 的 rootfs 或隔离的测试 rootfs，并核对根设备、自动挂载和扩容策略，防止访问或写入原 eMMC。生产 FIT 的原始 initrd 会按原 `.img` 的 root UUID 寻找磁盘根文件系统；它不是独立 RAM 测试系统。不能上传生产 FIT/initramfs 后就假定系统会仅在 RAM 中运行，普通 Web firmware 上传还会进入安装写入路径。
5. 控制通道、物理恢复路径、备份、RAM 测试和最终产物验证全部完成后，才评估实际刷写；之后仍需另行验收 Debian 首启、DHCP/factory MAC、p5 扩容、屏幕风扇与重启/断电恢复。

当前没有进行板卡重启、刷写、完整备份或串口连接，也没有实测可恢复的 U-Boot Web/网络控制通道或确认物理恢复路径。R4 本地打包/独立静态审计及完整 Linux regressions 已通过；新增两个编译检查目标的再验证、主机导出复制仍待结果，未来工作流未 dispatch，远端发布未确认。
