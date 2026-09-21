# 2026-09-13 原厂分区格式 R4 候选记录

> 历史发布范围：本文中的单次 workflow 同时发布 `.tar` 与 `.deb` 是 2026-09-13 当时的
> 流程记录，不代表当前发布模型。当前 Firmware 与 Display 使用独立 workflow / Release；
> 下方历史文件、哈希和未发布状态保持原意。

**R4 固件已经生成，独立最终静态审计通过；没有上板启动或刷写。**
这是保留原厂分区的格式转换结果，不是新 GitHub Actions 构建成功的声明，
也不是全部硬件功能已通过验收的声明。

## 文件

| 文件 | 字节数 | 用途 |
| --- | ---: | --- |
| `Armbian-trixie-6.18.51-e87n-r4-uboot-firmware.tar` | 793057280 | 未压缩 USTAR，约 756.32 MiB；实验性系统固件 |
| `e87n-display_1.0.0-1_all.deb` | 24612 | 同一源码的独立小屏安装包；显示程序本轮未改版本 |

```text
b3587a5377edf7c95f0d620eb038e67643287ac75629f20cc1b071e5dfa47545  Armbian-trixie-6.18.51-e87n-r4-uboot-firmware.tar
ae08b9e8315176b25533bc600efb17be23b4819ba21dfb7fbd4df48f9dd4615d  e87n-display_1.0.0-1_all.deb
```

本地交付目录为 `output/factory-candidates/20260913-r4/`，由 Git 忽略。
该目录不是 GitHub Release；当前未触发新的云端工作流，也未上传这两个二进制到 Release。
工作流仍只接受一次无参数手动启动，之后自动构建、生成 tag、发布 `.tar` 和 `.deb`。

TAR 的成员恰好为：

| 成员 | 字节数 |
| --- | ---: |
| `sysupgrade-edgepi-e87n/kernel` | 22345728 |
| `sysupgrade-edgepi-e87n/root` | 770703360 |
| `sysupgrade-edgepi-e87n/CONTROL` | 875 |

只有前两个成员是厂商写入 payload；CONTROL 记录来源与校验信息。
root 为 735 MiB ext4，使用 4096 字节块，实际占用 164659/188160 块。
首次启动的专用服务只扩展既有 p5 内的 ext4，不改 GPT。

## 来源与处理

输入是 [Actions 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588)
原始 Debian 13 / Linux 6.18.51 最小系统镜像，构建源码 `2a60011f98d33b9cc38c005061ec8935e029874c`。
原始未压缩整盘文件 SHA256：

```text
289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9
```

转换在 ARM64 Debian 13 Linux VM 中完成，源镜像前后摘要一致。只修改新私有副本：

- 将原 bootfs 放入 rootfs `/boot`，移除旧 `/boot` 挂载，保留 root UUID。
- 屏蔽通用分区扩容，加入严格核对原厂布局的 ext4 扩容和 factory MAC 服务。
- 清除可重新获取的 APT lists；不裁剪必要模块和系统程序。
- 重新建立同 UUID 的 ext4，以 GNU `cp -a --preserve=all` 复制，逐项比较文件摘要、
  属主、模式、硬链接、符号链接及 xattrs，包括根目录元数据。
- 更新最终 DTB 的 1 GiB 内存、固件保留区和 UUID bootargs，与源码 902 的板级修正对应。
  本轮没有重编译全部内核；902 已通过真实源码的无 fuzz dry-run，下一次工作流会重新构建。
- FIT 使用 LZMA 内核 5993493 字节、原始 initrd 16328217 字节、DTB 21479 字节。
  内核 load/entry 为 `0x40000000`；实际 Image 头 text_offset=0、image_size=`0x1690000`、flags=`0xa`。
  按 [ARM64 启动协议](https://docs.kernel.org/arch/arm64/booting.html)校验有效占用与 2 MiB 对齐。

早期 V3 使用旧的内核加载地址，**已废弃，不应刷写**。不要依据文件大小或旧的静态 PASS
选择 V3；本记录只对应上面的 R4 SHA256。

## 验证及修复记录

- R4 文件系统完整复制比较通过；最终 FIT/USTAR/ext4 已形成。
- 原组装调用在最后审计阶段因 VM 的 3.9 GiB `/tmp` tmpfs 满而退出非零，没有对外暴露成功产物。
  失败日志保留。审计器现改用磁盘 `RUNNER_TEMP` 或 `/var/tmp`，并先检查剩余容量。
- 对**同一 R4 TAR 字节**重新执行独立 `verify-factory-firmware.py`，退出码 0，随后才以
  不覆盖已有文件的方式导出候选。不能把这个补充验收描述成原组装调用或新 Actions run 退出 0。
- 真实 TAR 同时通过严格主机解析器和未修改的 MediaTek C 解析器，kernel/root 偏移及长度一致。
- FIT SHA256、有效内存范围、加载对齐、1 GiB DTB、固件保留区、唯一启动配置、精确 bootargs
  通过；拒绝额外 loadables、外部数据、外层保留表和意外 init 覆盖。
- ext4 只读 fsck、UUID/fstab、原厂分区容量与末尾擦除保护、helper/service 完整性通过。
- 实际包内 SSH 默认配置、DHCP、上海时区、中文 UTF-8、签名 APT 源、小屏程序、风扇/温控
  及平台配置静态审计通过；这些不是物理功能测试。
- 24 项固件夹具（含厂商解析器）、24 项 rootfs 适配测试通过；85 项 CI/发布测试通过。
- 完整 Linux `ci-regressions.sh` 在磁盘临时目录中退出 0，包括全部用户空间/镜像/平台套件。
  早期测试副本的 AppleDouble 附属文件、漏复制 docs、tmpfs 空间失败分别修复后重测，未隐藏失败记录。

## 刷写仍未获准执行的条件

原 OpenWrt 保持不变。没有执行设备重启、环境保存、GPT/FIP/SIMG 或系统刷写。
没有完成完整恢复备份、可恢复控制通道测试、板上临时启动和屏幕/风扇物理验收。
因此不能说“镜像驱动都正常”或“整个刷机任务已经完成”。

先建立串口或经过实测可恢复的 U-Boot Web/网络控制通道，准备独立恢复备份，
再用专门的临时 rootfs/RAM 测试环境验证；生产 FIT 内的 initrd 需要 ext4 rootfs，
**不能当作独立 RAM 系统直接测试**。通过这些条件之后，才评估在原厂 U-Boot 的
plain firmware 入口使用新 TAR。完整边界见 [U-Boot 固件契约](UBOOT-FIRMWARE.md)。
