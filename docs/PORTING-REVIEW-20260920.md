# E87N Debian/Armbian 路线复核（2026-09-20）

> 范围补充（2026-09-21）：candidate3 的结果对应旧 headless 配置。当前源码已预装显示包、启用 LCD/背光并继续调整板级补丁；需要针对新输入重新构建和验收。当前行为以 [DEFAULTS.md](DEFAULTS.md) 和 [display-fan.md](display-fan.md) 为准，下文已记录的候选结果不自动延伸到后续改动。

结论：Debian 13 ARM64 用户空间、Armbian 构建框架、MT7987 专用内核及 E87N 设备树、现有 U-Boot/FIT 的路线成立。candidate3 已完成完整构建、最终固件审计和同产物 Docker/QEMU 软件验收，但仍属于非官方板级移植，尚未完成实机验收。此前的诊断方法和测试包存在确定缺陷，不能把构建或上传成功当成硬件可用，也不能只凭网络失联就判定 U-Boot 损坏或内核未启动。

## 官方资料与可复用参考

| 来源 | 本次读取确认的事实 | 对 E87N 的意义 |
| --- | --- | --- |
| [Armbian 添加板卡文档](https://docs.armbian.com/build-framework/adding-a-board/) | 使用 board/family 配置、内核/启动补丁；提交前必须启动并检查外设 | 本仓库 board/family 架构符合这一方法，但未获得官方支持认证 |
| [Armbian boards](https://github.com/armbian/build/tree/main/config/boards) / [Filogic family](https://github.com/armbian/build/blob/main/config/sources/families/filogic.conf) | 本次目录未见 E87N；family 的 BOOT_SOC 分支只有 MT7981、MT7988；其他值报错 | 不能直接套通用 Filogic 启动链；本项目独立 family 和保留原启动链有依据 |
| [Armbian R4 配置](https://github.com/armbian/build/blob/main/config/boards/bananapir4.csc) | 普通 BPI-R4 是 MT7988 | 不能把普通 R4 当作同芯片板卡 |
| [Frank-W BPI-Router-Images](https://github.com/frank-w/BPI-Router-Images/blob/main/README.md) / [buildchroot.sh](https://github.com/frank-w/BPI-Router-Images/blob/main/buildchroot.sh) | 明确有 `variant=bpi-r4lite`；rootfs 支持 Trixie，架构 arm64 | 同平台 Debian 构建的直接参考，优先级高于电视盒子项目 |
| [Frank-W 6.18 内核](https://github.com/frank-w/BPI-Router-Linux/tree/6.18-main) | 包含 MT7987 时钟、pinctrl、MAC 和 R4 Lite 设备树 | 应作为驱动差异审核的主要参考；不能直接替换 E87N DTB 或启动组件 |
| [OpenWrt R4 Lite DTS](https://github.com/openwrt/openwrt/blob/main/target/linux/mediatek/dts/mt7987a-bananapi-bpi-r4-lite.dts) / [镜像定义](https://github.com/openwrt/openwrt/blob/main/target/linux/mediatek/image/filogic.mk) | R4 Lite 使用 MT7987，存在 LZMA FIT initramfs 构建路径，kernel load 为 `0x40000000` | FIT/RAM 启动方案有同芯片参考；它的交换机、GPIO、存储等布线不能照搬 |
| [ZJJCKA E87N 源码](https://github.com/ZJJCKA/EDGEPI-E87N) | 是 OpenWrt 项目；本次 Makefile 的内核系列为 6.12 | 作为本板 DTS、PHY、风扇/屏幕硬件资料，不以 OpenWrt 用户空间替代 Debian |
| [ophub 项目](https://github.com/ophub/amlogic-s9xxx-armbian/blob/main/README.cn.md) | README 支持平台为 Amlogic、Rockchip、Allwinner | 参考 CLI、配置和 Actions 发布方式；不复用其刷写地址、DTB 或内核更新工具到 MT7987 |
| [U-Boot bootm 文档](https://docs.u-boot.org/en/latest/usage/cmd/bootm.html) | 推荐 FIT；支持 kernel、ramdisk、FDT 组合 | FIT 与 Linux 发行版无绑定，rootfs 可以是 Debian ext4 |

读取的是当日远端内容；`main` / `6.18-main` 等分支会变化。真正采用新参考源码时需固定提交并记录补丁差异。

## 内核版本与支持边界

- [Debian 官方](https://www.debian.org/releases/trixie/) 当日显示 Debian 13.7，架构包含 ARM64。
- [kernel.org 版本信息](https://www.kernel.org/releases.json) 当日列出的最新 6.18 longterm 是 6.18.52。当前构建固定该维护版本。
- 当前 pin `a638fabe36f293e58ab6be002af04b866959c546` 的 Frank-W MT7987 源码确认为 6.18.52，实际 guest release 为 `6.18.52-current-edgepi-e87n`。
- 上游 v6.18.51 的 MediaTek DTS、时钟目录未见 MT7987 专用文件，`mtk_eth_soc.c` 无 MT7987 匹配。新 LTS 不等于完整本板支持，仍需要下游适配。不能泛化成“整个上游完全没有 MT7987 代码”。
- 本项目禁用了未验证 DVFS，部分硬件卸载也未提供。最小系统可以裁剪应用，但底层功能不能用编译成功代替硬件验证。

本次发现一个具体升级风险：[Frank-W 58f389fe](https://github.com/frank-w/BPI-Router-Linux/commit/58f389fe32c0ff80eaf88035c551a68be786e313) 修正 MT7987 PLL 注册参数。

已对照[上游 6.18.51 声明](https://github.com/gregkh/linux/blob/v6.18.51/drivers/clk/mediatek/clk-pll.h)和[6.18.52 声明](https://github.com/gregkh/linux/blob/v6.18.52/drivers/clk/mediatek/clk-pll.h)：前者接收 `struct device_node *`，后者改为 `struct device *`。当时旧 361 补丁传 `node`，属于升级风险；当前 6.18 路线已改用维护中的 Frank-W 基线，不再独立应用旧 361 补丁。这个历史风险不能直接解释实机失联，也说明升级不能只改版本号。

## 本次已确认的诊断缺陷

1. v6/v7 RAM initrd 的 `/init` 用外部 `cat` 读取 carrier，实际 cpio 没有 `cat`。命令失败被转换为 carrier=0，因此即使 Linux 正常进入 PID 1，也无法选择网口。现已用 shell 内建 `read` 替代。
2. beacon 原来只发往 `192.168.1.2`，本次主机地址却是 `192.168.1.20`。现同时支持两者；接收器也必须显式绑定实际地址。原先零 UDP 包不能证明内核没有启动。
3. `mounted_type` 把 `/proc/mounts` 的第二、三列读错。此问题影响挂载失败后的“是否已挂载”判断，不代表每次启动必然触发。现已按 source、mountpoint、filesystem 顺序修复。
4. Web `/result` 的 `success` 是启动前的接受结果。参考源码先退出 Web 服务再执行 `boot_from_mem`，所以继续轮询同一 Web Console 无法保证获得 bootm/Linux 日志。旧测试只捕获到上传 HTTP 请求，不能据此定位崩溃阶段。

修复后的主机行为测试与 FIT helper 测试合计 24 项通过。它们覆盖 carrier 读取、挂载表匹配和 UDP 发送行为；没有证明真实 PHY、内核或完整系统已运行。旧 v6/v7 文件不会因为源脚本修复自动变成新镜像，必须重新打包并记录哈希。

## 后续执行顺序

1. 固定一套可追溯的 kernel、modules、DTB、initrd。先修复测试包，以完整日志或实际 SSH 证明 RAM 启动；不要反复变更压缩、地址、内核和设备树后只看网页是否消失。
2. 按 Frank-W MT7987 适配审核时钟、pinctrl、PHY/MAC、MMC、USB/PCIe、watchdog、温控。保留 E87N 实际布线、RAM 容量和 factory 信息。若 RAM 测试仍无法观测，接入确认引脚和电平的 UART；Web Console 不能替代内核串口日志。
3. 完整候选必须用正常 initramfs 挂载 Debian rootfs 并进入 systemd PID 1，验证 SSH、双口 DHCP、DNS、APT、重启、存储、温控。当前 RAMdiag 的 shell PID 1 只是测试工具，不是最终系统。
4. 升级至经过适配的新 6.18 LTS 维护版，再做同套验证；不能用旧内核实测结果替代新内核验收。
5. RAM 启动及恢复链路通过后，才按已验证的原厂分区契约安装。完整 GPT 中间镜像不能当作厂商 Web Firmware 文件使用。
6. 同步设计 FIT、内核包、模块、DTB、initrd 的更新与回退。当前 kernel hold 可以防止不匹配，但不是完整的长期内核更新方案。显示包已经接入默认预装流程并保留独立升级接口，后续必须验证实际显示、背光、温度和风扇功能。

本次 candidate3 已在 ARM64 Docker/QEMU 中用最终 FIT、initrd 和 rootfs 完成启动与稳定性软件验收；没有上传、写入或启动实体 E87N，设备查询和真实 U-Boot 交接仍待实机验证。
