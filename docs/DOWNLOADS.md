# U-Boot 固件获取、发布状态与校验

> candidate3 已完成 Debian 13.7 / 6.18.52 构建、最终固件审计和同产物 Docker/QEMU 门禁，但它使用旧 headless 配置。
> 其产物摘要见[冻结验收记录](FINAL-VALIDATION-20260920.md)。当前源码已预装显示包并启用 LCD/背光，需要新的构建与验收；不能把下列文件当作新显示配置产物。6.18.51 / R4 内容均为历史记录。

本仓库公开的是构建源码、板级补丁、Debian 小屏程序、测试与文档。
**R4 本地已生成，对同一 TAR 的独立最终审计 EXIT 0，准确文件信息见下文。** R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。V3 因内核地址修正已废弃，不作为交付、不发布其哈希。主机导出及 SHA-256 比对已完成，未来新源码工作流未 dispatch，远端 Release 发布未确认。
唯一的[手动发布流程](GITHUB-ACTIONS.md)无需填写参数，每次重新构建本次运行选定的 main 提交，自动生成 tag 并发布到 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases)。只有手动 Run workflow 才执行；push、tag push 和定时任务均不触发。
`git clone` 不会下载之前本地生成的 `.img.xz`、内核包、完整日志或构建缓存。
可按 [构建指南](BUILDING.md) 自行生成，或在 [GitHub Actions](GITHUB-ACTIONS.md) 对应 run 成功并上传 artifacts 后取得文件，再校验。工作流文件存在、任务开始运行与成功产物上传是不同状态；artifacts 也不等同于 Release。

## 冻结 candidate3（旧 headless 配置，本地验收记录）

```text
文件名：Armbian-trixie-6.18.52-e87n-679b7d5-uboot-firmware.tar
SHA-256：efef220d81ad7f97155cc82d9246c3e1443ce8e4b9fa544002e39e050c2cc35d
显示包：e87n-display_1.1.1-1_all.deb
显示包 SHA-256：cc8bf0e71ae852c1e284c127ed00ee0460a0f731f4d0da99d390a05145327a12
QEMU 验收：PASS
硬件验收：未完成
```

当前 candidate3 的本地文件和完整证据不写入 Git；GitHub Release 需要手动运行唯一工作流生成。

## 历史 R4（不是远端发布记录）

```text
文件名：Armbian-trixie-6.18.51-e87n-r4-uboot-firmware.tar
大小：793057280 字节（约 756.32 MiB）
SHA-256：b3587a5377edf7c95f0d620eb038e67643287ac75629f20cc1b071e5dfa47545
root：770703360 字节（735 MiB）
kernel FIT：22345728 字节
CONTROL：875 字节
独立最终审计：EXIT 0
```

源 RAW SHA-256：`289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9`，来自历史 Actions 34737922588。本轮完整 ext4 复制比较通过，修正 DTB/bootargs 和 FIT 内核地址；没有完整重编新 Armbian/内核。首次审计因 `/tmp` tmpfs 满失败，改用磁盘临时目录后独立复验**同一 TAR**全部通过，再以不覆盖已有文件的方式暴露 R4；详见[固件记录](UBOOT-FIRMWARE.md)。

R4 本地已生成，独立最终审计 EXIT 0；factory 24 项、root adapter 24 项通过，完整 Linux `ci-regressions.sh` 已在磁盘临时目录运行并 EXIT 0。CI 85 项已再次通过；新增 runtime/root preparer 两个编译检查目标也已复验通过。主机导出与两文件摘要比对已完成；这不是硬件验收。 此处不提供未经确认的远端下载地址；未来工作流未 dispatch、远端发布未确认。收到本地交付文件时须核对以上 R4 摘要，正常安装仍须满足[首启准备](first-boot.md)。

本轮另已生成独立显示包：

```text
文件名：e87n-display_1.0.0-1_all.deb
SHA-256：ae08b9e8315176b25533bc600efb17be23b4819ba21dfb7fbd4df48f9dd4615d
```

显示包代码与版本未改变，这是新生成文件的摘要；不能与历史同版本包混用哈希，也不声明两次打包逐字节相同。此记录不表示已经上传远端 Release。

## 下载入口：tag Release

在仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 中选择版本，下载正文链接或 Assets 中的两个二进制文件：

- `<basename>-uboot-firmware.tar`：未压缩 USTAR 系统固件，已预装屏幕包。`sysupgrade-edgepi-e87n/kernel` 为 LZMA 内核 + 原始 initrd + DTB 的 FIT，`root` 为含 `/boot` 的 Debian ext4，另有 `CONTROL` 元数据。
- `e87n-display_<版本>_all.deb`：独立屏幕控制安装包；在已经运行的 Debian/Armbian 中按[安装说明](DISPLAY-PACKAGE.md)安装或升级。

Release 正文包含这两个文件的 SHA-256。下载后运行 `sha256sum 文件名`（macOS：`shasum -a 256 文件名`），与该版本正文比较。GitHub 自带的 **Source code (zip/tar.gz) 只是源码，不是固件**。内核包和审计日志保留在 Actions artifacts，不增加普通用户需要下载的安装附件。

TAR 外层不能重新压缩或换成 SIMG/GPT/FIP 文件；`sysupgrade-` 前缀不表示 OpenWrt rootfs，也不允许 LuCI sysupgrade。原厂 U-Boot Web plain firmware（类型 `fw`）的参考路径先写 p5 rootfs、再写 p4 FIT kernel，并在 root payload 后擦除 512 KiB，不写 GPT/FIP/环境。完整 `.img` / `.img.xz` 只作中间产物或历史证据，**不是可刷写附件**。详见[固件格式](UBOOT-FIRMWARE.md)。

需要发布新版本时，仓库维护者只需运行这一个工作流：

1. 打开 [E87N Debian 13 release](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)，点击 **Run workflow**。
2. 保持默认分支 `main`；无需填写任何参数，没有 tag、run ID 或内核输入框。
3. 点击 **Run workflow**。验证成功后，系统固件和显示包并行构建；系统 job 需完成中间镜像审计、TAR 转换、最终固件审计和同产物 QEMU 验收，全部成功后由 release job 自动生成并发布 `e87n-trixie-6.18.52-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`。

固定目标为 Debian 13 Trixie / Linux 6.18.52。每次手动运行实际重建当次 main 提交，不选择或复用历史成功构建；没有第二个发布入口。自动生成 tag 仅发生在这次手动运行内部，tag 指向镜像实际构建的提交，不会跟随运行期间更新的 main 移动。

这是操作说明，**不是该 tag 已经发布的声明**。遇到同名 tag/Release 时不会覆盖；上传或核对失败时保留已有 draft/tag 供检查。需要重试发布时，重新点击 **Run workflow** 发起一次新的手动运行，以获取新的 run ID、重新构建并生成新 tag；不要通过重跑失败 job 重试发布，因为重跑沿用原 run ID。

Release 下载不受 Actions 的 14 天保留期约束。候选仍标记为 Pre-release，硬件状态见下文；大镜像和 `.deb` 不进入 Git 源码历史。

## 历史最小配置构建证据与 Actions 下载（不可刷写）

最小配置以 [DEFAULTS.md](DEFAULTS.md) 为准：`root` / `doumao`、SSH 22、DHCP、上海时区、中文 UTF-8 和正常 APT；`e87n-display` 作为独立包发布，额外存储默认 `E87N_EXTRA_STORAGE=no`。历史 run `34737922588` 的源码为 `2a60011`，镜像校验值与审计证据见[当次记录](ci-keygen-fix-20260913.md)，不代表当前 main 已完成新构建或发布。

在上述成功 run 页面的 **Artifacts** 下载：

- `e87n-trixie-6.18.51-candidate-34737922588-1`：完整镜像、内核/DTB/BSP 包、校验清单、元数据和日志。
- `e87n-display-candidate-34737922588-1`：独立 `e87n-display_1.0.0-1_all.deb`、校验清单和日志。

Artifacts 保留 14 天，下载通常需要登录 GitHub；它们不是永久 Release 附件。下载并解开 artifact 后，在其根目录运行 `shasum -a 256 -c SHA256SUMS`（Linux：`sha256sum -c SHA256SUMS`），同时核对 `build-metadata.json` 的源码提交、成功结果和静态审计状态。历史整盘镜像不是新 TAR，不能直接刷写，也不能用其审计结果证明 902、factory MAC 或 p5 扩容适配已通过。

VM 用户空间集成只操作可丢弃 rootfs 副本，见 [TESTING.md](TESTING.md)，不能把这些经测试修改的副本作为交付镜像。这里的备用镜像是该历史成功 Actions 的原始产物，能否下载取决于 artifacts 是否仍保留。2026-09-13 11:10:49 CST 完成的旧配置 VM 构建已被替代，不代表新最小配置构建完成。

只有成功 run 中与目标提交、配置、日志及校验清单一致的 artifacts 才能作为该次构建产物下载。显示包的独立版本与升级方法见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)；云端运行与获取方式见 [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md)。上述历史文件是 Actions 候选产物，不是已通过实机验收的发行版。

## 历史本地屏幕/风扇候选（旧配置）

以下文件、大小和哈希仅保留历史含义，不对应当前最小配置。

2026-09-13 屏幕/风扇版：Debian 13.6 Trixie + Linux 6.18.51-current-filogic。
完整构建、真实镜像只读审计、Mac 导出与文件校验已完成，但尚未上板测试。
这不是在 GitHub 发布了二进制镜像的声明。

```text
文件名：Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
压缩大小：181175072 字节（约 173 MiB）
压缩 SHA-256：25112263959e5eb133a959591bfc08fa39168853c8915577ead51efede2a3c8f
解压大小：1170210816 字节
解压 SHA-256：679a35899f722157ecfe5da5cef5ed18c2c1cbbf13dbbe1bea41043c1827f543
```

原构建机本地交付目录为 `output/releases/2026-09-13-e87n-trixie-lts-6.18.51-display-fan/`，
由 `.gitignore` 排除，不是可在 GitHub 浏览的目录。详细证据见 [候选记录](candidate-display-fan-20260913.md)。

## 收到新 TAR 后检查

在构建主机上核对该次发布的文件名、大小和 SHA-256，确认外层为未压缩 USTAR、三个普通文件路径正确，并检查本次最终固件审计日志。完整检查入口为：

```sh
sudo -n python3 scripts/verify-factory-firmware.py /path/to/candidate-uboot-firmware.tar
```

这是 Linux 主机对普通文件的离线审计，不连接板卡。上述准确 R4 TAR 已独立完成全部检查，EXIT 0，V3 旧地址构件不能交付。`<=768 MiB` 只限制未压缩 TAR 的静态大小，不证明 Web 空闲 RAM 足够；R4 静态 PASS 不等于可以立即刷写。新增编译目标再验证、导出及硬件状态见[系统状态](SYSTEM-READINESS.md)。

## 历史压缩镜像的只读校验

在存放候选文件的目录执行；以下沿用历史文件名示范，只读检查不刷盘。新产物可能同名，必须与其自己的 run、配置及校验清单比对，不能使用上面的历史哈希验收新配置：

```sh
xz --test Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
shasum -a 256 Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
xz -dc Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz | shasum -a 256
```

Linux 可用 `sha256sum` 替代 `shasum -a 256`。若同时收到完整交付目录及 `SHA256SUMS`，
在该目录使用 `shasum -a 256 -c SHA256SUMS`；清单应与实际交付文件集合匹配。
哈希一致验证文件内容，不代表可信签名、硬件功能正常或刷写安全。

文件名和内核版本相同不代表同一个构建：2026-09-12 的旧 Trixie 文件缺少新增屏幕支持，
压缩 SHA-256 是 `32474999d280d4a9057985c6ba6985b1ed5f223584b7f8fe1809f9e4f48cb33e`。
它也不是上面记录的 9 月 13 日历史屏幕/风扇候选。

## 禁止当作直接刷机指南

完整 `.img` / `.img.xz` 包含 GPT，仅为中间产物或历史证据，不可刷写；新 TAR 面向原厂 U-Boot plain firmware，不能用于 LuCI 或 SIMG/GPT/FIP 入口。

当前没有板卡重启、刷写、串口连接、完整恢复备份或板上 RAM 测试记录，也没有 U-Boot 控制通道实测结果。必须先完成 RAM 测试和恢复备份，确认串口或已经实测可恢复的 U-Boot Web/网络控制通道、本机加载范围及物理恢复路径，满足用户“完全准备好再刷”的条件。优先评估网络 U-Boot，串口不是唯一选项。正常 TAR/FIT 启动使用磁盘 root UUID，诊断 RAM 启动需另备仅驻留 RAM 或隔离的测试 rootfs。下载成功、哈希匹配、TAR 小于上限或历史构建通过都不能替代这些条件。先阅读[固件契约](UBOOT-FIRMWARE.md)与[首启准备](first-boot.md)。
