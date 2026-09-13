# E87N 原系统只读核对：屏幕与风扇 — 2026-09-13

> 历史硬件参考，记录截至当天 08:14 的状态。之后已完成 Debian 原生屏幕/风扇候选构建，见 [新版记录](candidate-display-fan-20260913.md)。下文“待移植/没有生成”描述的是当时状态，不是当前源码能力。

本记录来自用户提供的 E87N，检查于 2026-09-13 08:14 CST 结束。SSH 使用已有主机密钥严格校验；只读取已安装程序、指定配置、设备树及 sysfs，并把三个程序复制到本地私有参考目录。没有刷写、重启、安装软件、执行屏幕/风扇控制命令或修改设备配置、PWM、网络。没有读取密码文件，也没有把登录凭证写入仓库。

**这些是原 OpenWrt 系统的运行证据，不是 Debian/Armbian 的上板验证。** 已交付的 Debian 13 / Linux 6.18.51 镜像没有因此改变，NV3007 仍未纳入该候选。

## 实际运行版本

- `ubus call system board`：`EdgePi E87N`、`edgepi,e87n`，OpenWrt SNAPSHOT `r35813-22a6103dad`，构建说明 `2026-08-14 By ZJJCKA`。
- `uname`：Linux **6.18.44**，AArch64，SMP。这不是之前的 6.12 参考版本，也不是目标 6.18.51。
- 在线 CPU 为 `0-3`；`MemTotal` 为 `1011132 kB`。原系统识别约 1 GB 内存，不意味着候选 DTB 启动时也能得到相同的 U-Boot 内存修正。
- 已安装 `kmod-fb-tft-nv3007`、`kmod-backlight-pwm`、`kmod-hwmon-pwmfan`、`luci-app-display`、`luci-app-fanctrol`。
- 未认证访问两个 LuCI 页面返回 HTTP 403；本轮通过 SSH 读取真实后端，没有调用 LuCI 的保存、切换、重启等操作。

## 屏幕与背光

| 项目 | 实际证据 |
| --- | --- |
| 驱动 | `fb_nv3007`、`fbtft` 已加载；`spi0.0/driver` 指向 `fb_nv3007` |
| 帧缓冲 | `/dev/fb0`；sysfs 名称 `fb_nv3007`，`virtual_size=428,142`，16 bpp |
| 设备树面板 | `newvisionu,nv3007`，原始宽 142、高 428，旋转 270° |
| SPI | `/soc/spi@11009800`（源码 `spi2`），52 MHz；Linux 枚举名为 `spi0.0` |
| GPIO | reset GPIO10 active-low；D/C GPIO11 active-high |
| 刷新参数 | DT/日志 `fps=100`，只是配置值，不是实测帧率 |
| 背光 | `pwm-backlight`，`/sys/class/backlight/backlight`，PWM 通道 2，周期 50000 ns |
| 当前设置 | UI 配置亮度 20%，`brightness=actual_brightness=21`，`max_brightness=26`，`bl_power=0` |
| 当前硬件 PWM | requested duty 41176/50000 ns，normal polarity；actual 41165/49991 ns |

背光电路为 active-low，但原系统 **没有请求 PWM 驱动的 inverted polarity**。安装的 `display-control` 在用户态反算亮度：

```text
raw = (max_brightness * (100 - percent) + 50) / 100  # 整数除法
```

这使 20% 映射到索引 21。表项依次为 `0,10,...,250,255`，索引与实际占空比不是同一数值。关屏写入最大索引 26，而不是 0；`bl_power=0` 是正常上电状态，不代表关屏。不得把常见的“brightness=0 即关闭”规则直接用于这块板。

原机 `display` 是 ARM64 ELF，动态解释器为 `/lib/ld-musl-aarch64.so.1`，没有 section header。Debian 默认用户空间不同，不能把该预编译程序当成已验证的 Debian 可执行文件。复制件仅用于静态参考，没有执行，也没有注入候选镜像。

本地参考仓库缺少完整可构建的显示渲染器源码：`package/luci-app-display/Makefile` 包装现成二进制并声明 FreeType/C++ 等依赖，没有获取或编译渲染器的步骤；`hiveton/src/data/data_network.c` 只是孤立的数据采集文件，不是完整 UI/LVGL/字体工程。原机和本地两份 ELF 均依赖 musl、FreeType、C++/GCC 运行库。候选没有这些已配套验证的显示依赖，因此后续必须取得完整源码或重写渲染器，不能只复制 LuCI 包或 ELF。

## 风扇与温度

| 项目 | 实际证据 |
| --- | --- |
| 服务 | `/bin/sh /usr/bin/fancontrol`，由 procd 管理；不是当前公开源码里的 Go 版本 |
| 控制接口 | 按 `type=pwm-fan` 查找 `/sys/class/thermal/cooling_device*`，写 `cur_state` |
| 当前档位 | `max_state=24`（0–24 共 25 档），`cur_state=13` |
| PWM | 通道 1，50000 ns，normal polarity；hwmon `pwm1=130`、`pwm1_enable=1` |
| 实际 PWM | requested duty 25490/50000 ns；actual 25479/49991 ns |
| 当前用户设置 | `enable=1`、`temp_sources=cpu`；20–110℃所有曲线点均为 55% |
| 状态文件 | `speed=55`、`state=13`、`error=`；CPU 来源有效 |
| 温度快照 | CPU 约 58.8–59.7℃；`mdio_bus:03` PHY 温度约 43.5℃；采样并非原子同步 |
| 转速反馈 | 没有发现 `fan*_input`；`speed=55` 是软件控制百分比，不能写成 RPM 或实测转速 |
| NVMe 温度 | 服务报告 NVMe1/2 missing；不能据此断言 PCIe/插槽损坏 |

风扇设备树表是 `0,10,...,220,240,255`，共 25 项。55% 经状态量化落到 state 13，最终 PWM 是 130/255，约 51%，所以 UI 百分比与占空比也不能混用。

原机脚本实际公式是 `state=(percent*max_state+50)/100`，采用整数除法。同样的 55% 放进候选四档表会得到 state 2、PWM 192/255（约 75.3%），不再是原来的力度。CPU 来源在 CPU/SoC thermal zones 内取最高温；当前只选择 CPU，PHY 43.5℃虽然显示在状态文件中，但没有参与本次控温。

原脚本失去所选温度源、遇到非法配置或写入失败时会尝试全速，正常退出及 INT/TERM 也通过 EXIT trap 尝试全速。不过 `enable=0` 明确要求持续停扇，并会覆盖温度失效保护；SIGKILL、堵转及接口失效不受保证。`--stop` 即使全速写入失败也可能返回 0，状态文件的 `speed=100` 不能作为硬件成功证据。这些行为不应不加修改地移植成 Debian 的安全承诺。本轮没有调用这些控制分支。

原 CPU thermal zone 使用 `step_wise`，trip 为 critical 125℃、hot 120℃、active 117/115/85/40℃；同一 `pwm-fan` 的 cooling maps 关联了其中 115/85/40℃。这些只是原系统软件阈值，不是已确认的芯片安全额定温度。原机内核 governor 与用户态直接写同一 cooling device，脚本没有接管或仲裁机制，因此存在互相覆盖的路径；不能假设内核一定及时覆盖用户态错误控制作为可靠兜底。本轮没有负载或控制试验，没有复现温区切换下的竞争时序。

目标候选当前使用四级 `<0 128 192 255>`、50/65/75℃内核自动调速，不运行 OpenWrt 守护程序。不能把原 UI 的 25 档状态原样写入目标四档表；迁移用户态曲线前必须确定唯一控制者及故障兜底。当前用户的“全段 55%”是个人配置，不应成为 Debian 默认散热曲线。

## 版本与本地参考件

原机三个文件已经 SCP 到本地，传输后 SHA-256 与原机读取值逐一一致。复制件保存在被忽略的 `output/` 私有参考目录中，目录 0700、文件 0600，不提交 Git。原始配置未整体导出；以下参考件不是可发布软件包或固件备份。

| 原机文件 | SHA-256 |
| --- | --- |
| `/usr/sbin/display` | `8dc6d9af9c64b64dee44306d9e2b8e206882ecf79c7e3ad0c9fa1d0ff1722cfa` |
| `/usr/sbin/display-control` | `8ea2c09b20386c68bbf5abd1b7a25e2cde750201f427fed605ea737f511007fc` |
| `/usr/bin/fancontrol` | `0f3776711a46f788d9eea90c02f3220cd145e34d9cd27ef26d4049418a3a7bb9` |

本地 E87N 源树的 `display` 哈希为 `8c7cb32832bf9e27dd3a4ebf92f725006a2f333823f2935eb7899b4d84b27519`，`display-control` 为 `a75d0e1c2677967ab35e3ef58df5c69ce3048a33f82ee286a1ca91b84a78fed9`，均不同于原机。源树控制脚本还新增了持久化、帧缓冲预览和恢复等功能。原机行为不能直接充当该源码版本的验收结果。

原机控制循环是 20 秒，本地改为 2 秒并加入进程/设备恢复。两份显示 ELF 还存在数据源字符串差异，不只是调试符号差异；这属于静态分析，没有执行它们来验证实际采集和显示行为。

公开 [E87N 项目](https://github.com/ZJJCKA/EDGEPI-E87N) 的当前说明包含屏幕、Go 风扇守护等定制；它是硬件和算法参考，不是目标 Debian rootfs。用户提到的两个参考固件尚未收到明确文件路径、附件或下载地址，本轮没有解包或验证它们。

## 下一版移植与验收顺序

1. 将 NV3007 内核驱动移到固定的 6.18.51，启用匹配的 fbtft、SPI 和背光配置；按实际控制方式审查 DT。原机 6.18.44 的 `.ko` 不能直接当作 6.18.51 模块使用。
2. 获取可构建的显示程序来源及依赖，或实现 Debian 原生帧缓冲程序；替换 UCI/procd/LuCI 服务依赖，保留反向亮度映射和关闭语义。
3. 风扇先保留候选的内核自动控制作为独立基础；如加入曲线 UI，需明确内核/用户态仲裁、失温全速、进程停止及异常恢复。禁止直接启用两个未协调的控制器。
4. 用两个明确的参考固件核对 DTB、模块、显示程序和引导布局，分别记录版本与哈希；不要把参考固件本身当作可直接启动的 Armbian。
5. 新补丁和用户态程序完成编译、静态检查后生成另一个候选，保留旧产物。通过隔离、可恢复的临时启动测试实测屏幕、亮度、温度与风扇，才能宣称 Debian 上对应功能正常。

本轮只完成了原系统取证和差异分析；没有生成包含这些新功能的镜像、没有 Armbian 首启结果，也没有做整盘备份或恢复测试。首次启动和写入仍遵守 [首启边界](first-boot.md)。
