# E87N 最终软件验收记录（2026-09-20）

本记录对应源码提交 `679b7d5fa8ca650b9a3db43b3605df0966a96ac5` 的 candidate3。
构建配置固定为 Debian 13.7 Trixie ARM64、Linux 6.18.52、Armbian 提交
`7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，内核源码为 Frank-W
`a638fabe36f293e58ab6be002af04b866959c546`。固件格式为
`e87n-uboot-firmware-tar-v2`。

## 产物

| 文件 | SHA-256 |
| --- | --- |
| `Armbian-trixie-6.18.52-e87n-679b7d5-uboot-firmware.tar` | `efef220d81ad7f97155cc82d9246c3e1443ce8e4b9fa544002e39e050c2cc35d` |
| `Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.52_minimal.img.xz` | `0d806f3a245c4ecfa594a1d2875fc9658fc9b3ca563f54f8aa686f5dcb1216a6` |
| `e87n-display_1.1.0-1_all.deb` | `cc8bf0e71ae852c1e284c127ed00ee0460a0f731f4d0da99d390a05145327a12` |

正式用户下载附件是 U-Boot TAR 和独立 `.deb`；`.img.xz` 是构建中间产物，不能直接交给原厂 U-Boot plain firmware 入口。

## 已通过

- 完整 Armbian/内核构建、DTB、initrd、模块、PHY 固件和 rootfs 一致性审计。
- 厂商分区适配、FIT 地址/大小、ext4 UUID、U-Boot TAR 和独立固件审计。
- 原生 ARM64 Docker 中用最终 FIT、最终 initrd 和最终 rootfs 私有副本启动 QEMU `virt`。
- 实际 systemd PID 1、root 密码 SSH、DHCP、DNS、Asia/Shanghai、`zh_CN.UTF-8`、APT update/install。
- 内核与 DTB 包 hold、minimal 软件策略、warm reboot、cold reboot、文件持久化和新实例 SSH host key。
- `e87n-display` 的安装、同版本重装、配置保留、卸载和重新安装生命周期。
- 完整 QEMU 报告：`status=PASS`，内核版本 `6.18.52-current-edgepi-e87n`。

QEMU 报告绑定 firmware TAR、显示包、源码提交、run ID 和 attempt；报告与证据保存在本次本地构建输出和 CI artifact 中。

## 尚未通过的边界

QEMU `virt` 不模拟 MT7987 MAC/PHY、eMMC、SPI/NV3007、PWM 风扇、factory MAC、LVTS 校准和 E87N 原厂 U-Boot 交接。因此本记录不能证明真实板卡已经可刷、可启动，不能证明两个物理网口、屏幕、风扇、USB/NVMe 或断电恢复正常。

刷写前仍需在设备上完成：可恢复的 U-Boot Web/网络路径确认、完整 eMMC/boot 区备份、RAM/诊断启动、首启网络和存储检查。当前没有把 candidate3 宣称为实机验收或生产安全版本。

## GitHub Actions

工作流保持 `workflow_dispatch` 手动触发、无输入；只有 validate、display、image 和同产物 QEMU 验收全部通过后，release job 才创建唯一 tag 并上传两个正式附件。candidate3 是本地验证记录，不等同于远端 Release 已存在。
