# E87N Debian 原生屏幕与风扇支持

目标固定为 Debian 13 Trixie + Linux 6.18.51，不切换为 OpenWrt。此实现参考原系统的硬件接口，但不安装 LuCI/UCI/procd、原 musl 显示 ELF 或原风扇守护程序。它是新的本地命令行控制与小屏程序，不是原 LuCI 网页的复刻，也不会新开 HTTP 服务。

## 当前候选状态 — 2026-09-13

完整镜像构建已成功：`e87n-display-fan-build.service` 于 **2026-09-13 09:05:25 CST** 启动，**09:15:45 CST** 结束，退出码为 0。当天对实际 raw 镜像运行的 `verify-image --release trixie --require-usb-root --require-display-fan` 已全部通过，包含本页所述显示/背光程序和内核支持的静态检查。

压缩和 Mac 导出、33 项文件清单、xz 完整性及解压后 raw 校验均已完成。约 173 MiB 的新版镜像、精确路径及校验清单见 [本轮屏幕/风扇候选记录](candidate-display-fan-20260913.md)。9 月 12 日的 Trixie 与 Bookworm 镜像均为历史候选，不含本次显示支持；它们的哈希、导出及验证记录不构成本轮新增功能的证据。

**本轮没有访问、刷写、重启或测试实体板卡。** 新候选已构建并静态验证，Debian 下的物理屏幕、背光、风扇及首启仍待实机验收。

## 实现范围

- 当前 6.18 补丁集共 **13 个补丁**，其中加入 GPL NV3007 fbtft 驱动（保留作者和初始化序列，适配 6.18 Kconfig）；SPI/背光内建，`fb_nv3007` 模块配置为随启动加载。
- `/dev/fb0` 使用 428×142、16-bit RGB565；原 SPI 52 MHz、270°旋转保持，刷新配置上限改为 30 FPS。实际界面默认每 2 秒更新，不能把 30 当作实测帧率。
- 四页原生界面：`overview` 设备概览、`thermal` 温度及风扇、`network` 网卡字节计数、`storage` NVMe 温度。未发现的指标显示 `--`；没有测速线便不编造 RPM。Python/Pillow/DejaVu 字体由 Debian 软件包提供。
- 背光 PWM2、50000 ns、normal polarity；用户亮度在软件中反向映射。上电默认 raw 26（暗），显示服务应用保存的亮度，首次默认 20%。逻辑关闭写 raw 26，不能使用常见的 raw 0 或 `bl_power=4` 关闭方法。
- 风扇 PWM1、50000 ns，四级 `0/128/192/255`，50/65/75℃触发 1/2/3 档，迟滞 2℃。这些是控制阈值，不是芯片安全额定温度。没有用户态风扇写入者，不会与内核 governor 抢控制。
- 串口 ttyS0 保留；禁用 framebuffer console，避免控制台字符覆盖小屏。

## 登录 Armbian 后的命令

以下是新镜像中的命令，**不是让当前 OpenWrt 执行**。本轮构建过程不会自动向设备执行它们。

```bash
e87nctl status
e87nctl fan status
sudo e87nctl display brightness 20
sudo e87nctl display screen thermal
sudo e87nctl display off
sudo e87nctl display on
```

亮度范围 0–100；页面只能是 `overview|thermal|network|storage`。设置保存在 `/etc/e87n/display.json`，采用校验、锁及原子持久化；关闭时改亮度或页面不会偷偷点亮。`display apply` 仅应用已保存状态，不改配置，供显示服务的启动前步骤使用。

显示服务是 `e87n-display.service`。服务错误会以非零状态退出并重试，不把缺少帧缓冲或错误板型当成成功。它限制设备写权限到 fb0/背光，不能写 thermal/cooling/PWM sysfs。关屏不会停风扇，显示服务停止也不会改变风扇控制。

当前不提供任意手动风扇百分比或停扇接口。原系统“55%”会按其 25 档表量化成 PWM130；新四档表的状态编号含义不同，不能直接复用原插件输出。原机器的恒定 55% 是个人设置，不作为新默认值。

## 构建与离线验收

`build-armbian.sh` 把 `board-support/` 复制到 Armbian overlay，`customize-image.sh` 在新 Debian chroot 安装依赖、程序、默认配置和 systemd 启用链接，但不启动目标显示服务。

```bash
sudo bash scripts/verify-image.sh --release trixie \
  --require-usb-root --require-display-fan /absolute/path/to/new.img
```

验证器只读挂载其自行创建的 loop，检查实际镜像中的内核配置、DTB、模块、Python 语法、依赖文件、默认配置及服务链接；不会执行镜像程序，也不会接触板子的 eMMC。测试中的假 sysfs 和预览 PNG 只验证代码与布局，不能充当物理屏幕或风扇测试。

本轮已逐字节核对镜像中安装的 4 个 Python 文件、服务文件和默认 JSON，均与冻结输入一致；三组 fixture 测试在原生 Python 3.13.5、显式 `umask 022` 下复跑，42、38、32 项均通过。复测条件和保留的失败尝试将收录于 [本轮候选记录](candidate-display-fan-20260913.md)，这些测试结果仍属于离线代码验收。

在安装了 Pillow、DejaVu 字体及 `dtc` 的开发主机中复跑（只操作临时夹具，不要在设备上运行）：

```sh
umask 022
python3 -B tests/test-hardware.py
python3 -B tests/test-display.py
python3 -B tests/test-verify-display-fan.py
```

## 必须保留的实机边界

Linux 6.18.51 的 NV3007/fbtft/PWM 背光支持已随本轮完整镜像构建完成，真实镜像只读验证与本机导出校验均已通过，证据见 [本轮候选记录](candidate-display-fan-20260913.md)。本轮没有访问设备，不能以离线结果判断原设备当前状态，更不能把这些结果视为 Debian 实机测试通过。

首次必须在可恢复、隔离的启动环境下核验面板 probe、实际颜色/方向、0/20/100%亮度、开关与设置持久化，并观察 CPU 温度、冷却档位、真实风扇起转及负载温升。内核自动温控不等于断线、传感器失效、堵转或全部异常场景均有保证；CPU DVFS 仍禁用，WED 和其他待测项目也没有因本功能而变为已验证。

完整镜像仍包含新 GPT。原设备 U-Boot 加载能力、分区和恢复备份未验证前，不直接整盘写入 eMMC。继续遵守 [首启与写入边界](first-boot.md)，尤其默认 `root/1234`、串口自动登录、SSH root 登录及共享初始 SSH 主机密钥的隔离首启要求；新增显示支持不改变这些限制。
