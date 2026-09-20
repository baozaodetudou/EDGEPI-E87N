# EdgePi E87N Armbian / Debian

为 EdgePi E87N（MediaTek MT7987A、1 GiB RAM、eMMC）制作的非官方最小 Debian/Armbian 系统。
当前重构目标为 **Debian 13.7 Trixie + Linux 6.18.52 LTS**，采用持续维护的
[Frank-W MT7987 内核](https://github.com/frank-w/BPI-Router-Linux/tree/6.18-main)，
通过 Armbian 框架构建，保留 E87N 自身的设备树和原厂 U-Boot 分区契约。

当前冻结候选已完成完整编译、最终 U-Boot TAR 审计和同产物 Docker/QEMU 验收。
候选为 Debian 13.7 / Linux 6.18.52；QEMU 已验证 systemd、SSH、DHCP、DNS、APT、重启、持久化和独立显示包生命周期。
MT7987 实机启动、双网口、eMMC、USB/NVMe、温控、风扇、屏幕和真实 U-Boot 交接仍需上板验收。
详细结果见[最终验收记录](docs/FINAL-VALIDATION-20260920.md)、[路线复核](docs/PORTING-REVIEW-20260920.md)与[验收标准](docs/REFACTOR-ACCEPTANCE.md)。

## 系统配置

| 项目 | 默认值 |
| --- | --- |
| 用户空间 | Debian 13 ARM64，systemd 管理服务 |
| 登录 | `root`，公开初始密码 `doumao`，SSH 22，允许 root 密码登录 |
| 网络 | 两个有线口请求 DHCP；不预设路由、NAT 或 LAN/WAN 角色 |
| 软件管理 | Debian 签名源、`apt update`、`apt install` |
| 时区与编码 | `Asia/Shanghai`、`zh_CN.UTF-8` |
| 首次启动 | 自动生成独立 SSH host keys；没有强制创建用户向导 |
| 默认体积 | 命令行系统，不预装桌面、LuCI、Docker 服务或 RAID/LVM 管理套件 |
| 风扇 | 由内核温控；QEMU 只能验证系统集成，实机转速/温度仍待完成 |
| 小屏 | 独立 `e87n-display` Debian 包；驱动默认不自动加载，实机显示仍待完成 |

镜像使用独立的 `linux-image-current-edgepi-e87n` 和 `linux-dtb-current-edgepi-e87n`
包名，避免通用 Filogic 软件包替换本板内核。内核/DTB 保持锁定；板级升级要求成套
重建并验证 FIT、rootfs、模块和固件，不能仅更新 `/boot` 就认为 p4 启动镜像同步。

## 构建与版本来源

唯一版本清单位于 [e87n-build.json](userpatches/config/e87n-build.json)，包含发行版、
内核版本、完整内核提交、Armbian 提交和包名。构建采用固定提交，不随远端分支自动变化。
Debian 软件包从签名源获取更新；这不等于全部软件包的逐字节快照重现。

```sh
python3 scripts/build_config.py
./build.sh build
```

上述入口生成 Armbian 中间镜像。完整交付还须经过镜像审计、厂商布局转换、最终 TAR
审计和模拟启动测试，详见[容器验证](docs/CONTAINER-TESTING.md)及[验收标准](docs/REFACTOR-ACCEPTANCE.md)。
构建主机需要 Linux 或 macOS Docker；不要在目标设备上执行构建脚本。

Docker 构建环境定义在 [containers/build/Dockerfile](containers/build/Dockerfile)。
QEMU 在 Docker 内用本次生产内核、initrd 和最终 rootfs 副本启动 systemd，验证
SSH、DHCP、DNS、APT、时区/编码、重启及包安装。容器本身共用宿主内核，单纯 chroot
或 `uname` 不算目标内核启动测试。QEMU `virt` 不模拟 MT7987 外设，报告必须保留此限制。

构建记录包含实际源码摘要、内核/config 哈希、包版本、DTB/initrd/模块/PHY 固件和 root
UUID，使模拟测试和发布附件能够对应同一产物。

## 手动构建与下载

唯一 GitHub Actions 工作流为 [E87N Debian 13 release](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)。
手动选择 `main` 并点击 Run workflow，不需要填写输入；push、tag push 和定时任务不触发。
工作流生成包含版本、run ID、attempt 的唯一 tag，只有全部构建和模拟验收门禁通过才发布。

[Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 的交付文件为：

- `*-uboot-firmware.tar`：原厂 U-Boot plain firmware 封装；内含 FIT、Debian ext4 rootfs 和构建记录。
- `e87n-display_<版本>_all.deb`：可独立安装、升级的小屏软件包。
- SHA-256、内核配套包和构建/验证证据。

源码 zip/tar.gz 不是系统镜像。完整 GPT `.img` 是中间产物，不能直接通过原厂 Web
固件入口写入 eMMC。固件封装使用 `sysupgrade-` 目录约定，用户空间依然是 Debian。
当前本地 candidate3 已完成软件验收，但尚未声明已发布 GitHub Release 或完成实机刷写；[下载记录](docs/DOWNLOADS.md)中的 6.18.51 文件均为历史产物。

## 使用与维护

新系统通过验收并安装后，从路由器 DHCP 租约获取 IP：

```sh
ssh root@<设备IP>
passwd
systemctl status ssh
apt update
apt install --no-install-recommends curl
```

按[屏幕包说明](docs/DISPLAY-PACKAGE.md)安装独立包后，使用 `e87nctl` 配置页面、亮度和刷新周期。
没有真实转速反馈时显示 `--`，不把 PWM 百分比伪装为 RPM。屏幕硬件开发与验收放在完整系统之后。

- [默认配置](docs/DEFAULTS.md)、[网络说明](docs/NETWORKING.md)、[显示包](docs/DISPLAY-PACKAGE.md)
- [厂商固件契约](docs/UBOOT-FIRMWARE.md)、[工厂布局适配](board-support/factory-boot/README.md)
- [历史构建指南](docs/BUILDING.md)、[旧 README](docs/README-LEGACY-20260920.md)
- [来源与许可](NOTICE.md)

基础系统使用 Debian、systemd 和 Armbian 构建组件；OpenWrt E87N 源码提供板级硬件参考。
本项目没有官方 Armbian 板卡支持认证。新增代码遵循 GPL-2.0，第三方代码保留原许可；
MediaTek PHY 微码使用独立许可，不统一改为 GPL。
