# E87N Debian 原生屏幕与风扇支持

目标为 Debian 13 Trixie + Linux 6.18 LTS，当前审查版本为 Debian 13.7 / Linux 6.18.52，完整 release 为 `6.18.52-current-edgepi-e87n`；版本和源码 pin 以 [e87n-build.json](../userpatches/config/e87n-build.json) 为准。此实现参考原系统的硬件接口，但不安装 LuCI/UCI/procd、原 musl 显示 ELF 或原风扇守护程序。它是原生命令行控制与小屏程序，不会新开 HTTP 服务。

## 当前配置与历史候选 — 2026-09-21

当前最小镜像预装 `e87n-display`，启用 NV3007 与 PWM 背光设备树节点，提供模块自动加载配置和下一次启动启用的显示服务。相同版本的软件包仍独立构建发布，用于升级和重新安装，见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)。构建不再安装旧 headless 黑名单。2026 年 9 月 21 日已在真实设备上验证 Debian 13、`fb_nv3007`、中文字体、双网口卡片、背光命令和短时风扇测试，完整结果见 [FINAL-VALIDATION-20260921.md](FINAL-VALIDATION-20260921.md)。

[9 月 13 日历史屏幕/风扇候选](candidate-display-fan-20260913.md)保留其当次构建、静态检查、导出与哈希证据。9 月 12 日的 Trixie 与 Bookworm 镜像也属历史候选；这些文件均不能作为当前最小配置交付。

candidate3 的 Docker/QEMU 软件验收及独立显示包生命周期结果见[冻结记录](FINAL-VALIDATION-20260920.md)，其基础镜像使用旧 headless 配置。当前提交后的生产镜像仍须重新构建和发布；QEMU 不模拟这些外设，预览 PNG 也不能作为设备照片或实测证据。实机已验证的项目与尚未完成的光学颜色、长期散热边界见 [FINAL-VALIDATION-20260921.md](FINAL-VALIDATION-20260921.md)。

## 实现范围

- 当前使用维护中的 Frank-W MT7987 内核，加上 `userpatches/kernel/edgepi-e87n-6.18/` 的 E87N 补丁；包括 GPL NV3007 fbtft 驱动、GMAC aliases、1 GiB 保留内存及 LVTS 修正。SPI/背光内建，`fb_nv3007` 模块配置为随启动加载；补丁数量及摘要以本次构建 receipt 为准。
- `/dev/fb0` 使用 428×142、16-bit RGB565；原 SPI 52 MHz、270°旋转保持，刷新配置上限改为 30 FPS。实际界面默认每 2 秒更新，不能把 30 当作实测帧率。
- 四页原生界面：`overview` 设备概览、`thermal` 温度及风扇、`network` 双网口链路/地址/字节计数、`storage` NVMe 温度。未发现的指标显示 `--`；Python/Pillow、DejaVu 拉丁字体和 WQY MicroHei 中文字体由 Debian 软件包提供。
- 风扇默认由内核 `pwm-fan` + thermal `step_wise` 自动控制。界面显示 `AUTO`、`LEVEL`、`PWM%` 和 kernel policy；小屏不显示 tachometer RPM，也不把 PWM 或 cooling level 当作转速。
- 背光 PWM2、50000 ns、normal polarity；用户亮度在软件中反向映射。上电默认 raw 26（暗），显示服务应用保存的亮度，首次默认 20%。逻辑关闭写 raw 26，不能使用常见的 raw 0 或 `bl_power=4` 关闭方法。
- 风扇 PWM1、50000 ns，四级 `0/128/192/255`，50/65/75℃触发 1/2/3 档，迟滞 2℃。这些是控制阈值，不是芯片安全额定温度。没有用户态风扇写入者，不会与内核 governor 抢控制。
- 串口 ttyS0 保留；禁用 framebuffer console，避免控制台字符覆盖小屏。

## 登录 Armbian 后的命令

以下命令用于已启动的目标 Debian 系统。首次在可信内网通过 SSH 22 使用 `root` / `doumao` 登录，运行 `passwd` 改密后再操作；这些命令不用于当前 OpenWrt。

```bash
e87nctl status
e87nctl fan status
e87nctl fan test 1 5
e87nctl acceleration status
e87nctl display config
e87nctl display brightness 20
e87nctl display screen thermal
e87nctl display refresh 2
e87nctl display off
e87nctl display on
```

亮度为整数 0–100；页面为 `overview|thermal|network|storage`；刷新间隔为整数 2–60 秒。默认总览、20% 亮度、每 2 秒刷新。`display config` 只读显示校验后的已保存或默认配置，`display refresh` 保存刷新间隔。设置保存在 `/etc/e87n/display.json`，采用校验、锁及原子持久化；关闭时改设置不会偷偷点亮。`display apply` 仅应用已保存状态，不改配置，供显示服务的启动前步骤使用。使用非 root 管理用户时，写配置命令需要相应权限。

`e87nctl fan test LEVEL SECONDS` 是 root-only、0–30 秒的临时冷却档位测试，结束后恢复原档位；它不是持久手动模式，也不关闭 thermal 保护。显示服务是 `e87n-display.service`。服务错误会以非零状态退出并重试，不把缺少帧缓冲或错误板型当成成功。显示服务本身只写 fb0/背光，不写 thermal/cooling/PWM sysfs；关屏不会停风扇，显示服务停止也不会改变风扇控制。

当前不提供任意手动风扇百分比或停扇接口。原系统“55%”会按其 25 档表量化成 PWM130；新四档表的状态编号含义不同，不能直接复用原插件输出。原机器的恒定 55% 是个人设置，不作为新默认值。

## 构建与离线验收

`build-armbian.sh` 将显示包构建脚本、`packaging/` 和 `board-support/` 复制到 Armbian overlay；`customize-image.sh` 在新 Debian chroot 构建并用 APT 安装版本化 `e87n-display` 包。程序、显示配置和服务由 dpkg 管理，安装时应由 chroot 的服务策略抑制运行态启动。包不提供内核模块，也不改变 SSH、网络或风扇控制策略。

```bash
sudo bash scripts/verify-image.sh --release trixie \
  --require-usb-root --require-display-fan --require-system /absolute/path/to/new.img
```

验证器只读挂载其自行创建的 loop，检查实际镜像中的内核配置、DTB、模块、Python 语法、依赖文件、默认配置及服务链接；不会执行镜像程序，也不会接触板子的 eMMC。测试中的假 sysfs 和预览 PNG 只验证代码与布局，不能充当物理屏幕或风扇测试。

旧镜像的逐字节比较与历史 fixture 计数仅见[历史候选记录](candidate-display-fan-20260913.md)。当前代码与软件包测试结果见 [TESTING.md](TESTING.md)；不得把旧结果或示例预览写成新镜像或物理显示通过。

在安装了 Pillow、DejaVu 字体及 `dtc` 的开发主机中复跑（只操作临时夹具，不要在设备上运行）：

```sh
umask 022
python3 -B tests/test-hardware.py
python3 -B tests/test-display.py
python3 -B tests/test-verify-display-fan.py
```

## 必须保留的实机边界

NV3007/fbtft/PWM 背光支持需要随新内核和镜像重新验证。VM、副本和夹具结果不能判断原设备当前状态，也不等于 Debian 实机显示或散热测试通过。

首次必须在可恢复、隔离的启动环境下核验面板 probe、实际颜色/方向、0/20/100%亮度、开关与设置持久化，并观察 CPU 温度、冷却档位、真实风扇起转及负载温升。内核自动温控不等于断线、传感器失效、堵转或全部异常场景均有保证；CPU DVFS 仍禁用，WED 和其他待测项目也没有因本功能而变为已验证。

Armbian 中间镜像仍包含新 GPT，不能直接整盘写入 eMMC；正式固件使用最终 U-Boot TAR。继续遵守 [首启与写入边界](first-boot.md)：当前默认密码公开，首次只接可信内网并改密，SSH host keys 应在首次 SSH 前独立生成；旧候选的自动登录和共享初始密钥风险按其历史记录处理。当前 E87N DTS 已通过 902 补丁描述 1 GiB 内存和固件保留区，真实 U-Boot 内存交接仍需上板验证。
