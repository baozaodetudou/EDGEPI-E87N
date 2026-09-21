# E87N 小屏界面设计

当前布局版本：`e87n-display 1.3.1-1 / 428×142 / RGB565 / 三配色 / 八页面 / 不显示 RPM`。
渲染代码位于 `board-support/e87n/display.py`，预览命令为：

```sh
python3 -m e87n.display --preview /tmp/e87n-overview.png --screen overview
```

## 设计原则

- 428×142 是硬约束；所有页面都以此尺寸渲染，再写入 NV3007 framebuffer。
- 三套配色共享相同的数据语义和可用性规则；主题只改变颜色，实时遥测决定卡片、行和页面
  是否出现。
- 只使用大块面板、短标签和高对比纯色，适配 RGB565、低亮度和 270° 旋转。
- 重要信息优先级：有效网口链路/IP → CPU/RAM/温度 → 风扇控制状态。
- 缺失遥测对应的卡片或行直接隐藏并收紧布局，不保留空面板，也不把缺失值推断成 0 或
  `断开`。
- 屏幕不显示 RPM。E87N 没有可靠 tachometer 输入，PWM 与 cooling level 也不能换算成转速。

## 三种配色主题

| 主题 | 适合场景 | 视觉特点 |
| --- | --- | --- |
| `dark` | 默认、低亮度和长期运行 | 深蓝背景、青色主信息、绿色正常、橙色温度 |
| `aurora` | 希望重点状态更醒目 | 近黑背景、洋红主色、青色辅助色 |
| `light` | 光线较强的机房或桌面 | 浅灰背景、深色正文、高对比边框 |

三种主题都保持 428×142 输出，并且都不显示虚构的网速、协商速率或 RPM。它们只改变
颜色，不改变字段含义、网口资格或页面可用性判断；同一份实时遥测在三种主题下会得到
相同的动态收缩布局。

## Overview 布局

```text
┌──────────────────────────────────────────────────────────────┐
│ E87N / 系统                                运行 2d 03h        │
│ 设备状态 ● 在线                              428 x 142      │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ 网口1  ● 在线          192.168.20.201              eth0 │ │
│ └──────────────────────────────────────────────────────────┘ │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────────────┐ │
│ │CPU 24% │ │内存38% │ │温度58C │ │风扇 自动 75%             │ │
│ └────────┘ └────────┘ └────────┘ └────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

网口先按有效性筛选，再按接口名稳定排序并编号；没有资格的网口不保留空卡片。规则如下：

- `carrier=0` 的网口无条件隐藏，即使内核仍留下地址、RX/TX 计数或其他陈旧状态；
- carrier 已知为 up 的网口可显示；
- carrier 未知时，只有有效 IPv4 或全局 IPv6 才能让网口显示；
- 只有 link-local IPv6 不足以显示 carrier 未知的网口；
- 两个网口都可见时并排显示，只剩一个时该卡片占满整行。

采样层优先选择 `eth*`、`end*`、`enx*`、`lan*`、`wan*`，并排除 `lo`、`br-lan`、
`docker0`。当前内核快照没有可靠协商速率字段，因此界面只显示有效链路、接口名和地址，
不虚构 `2.5G` 或 `1G`。

## 页面

| 页面 | 显示内容 | 风扇字段 |
| --- | --- | --- |
| `overview` | 有效网口、IP、CPU、内存、CPU 温度、运行时间；缺失卡片/行隐藏 | `自动 PWM%` |
| `cpu` | CPU 使用率、1/5/15 分钟负载、当前频率、governor、驱动 | 不显示 |
| `memory` | 内存使用率、已用、可用和总量 | 不显示 |
| `thermal` | CPU/PHY 温度、thermal policy | `MODE`、`LEVEL`、`PWM` |
| `fan` | 内核控制模式、cooling level、PWM、policy | 完整显示；无 RPM |
| `network` | 可见接口的 link、IPv4/全局 IPv6、累计 RX/TX 计数 | 不显示 |
| `traffic` | 可见网口 RX/TX 累计总量、主链路和本地地址 | 不显示 |
| `storage` | 可用温度传感器和存储设备状态 | 不显示 |

八个页面 `overview`、`cpu`、`memory`、`thermal`、`fan`、`network`、`traffic`、`storage`
始终都可配置，运行时再按有效实时遥测判断是否可显示。页面轮换默认关闭；开启后 daemon
按配置顺序过滤所有不可用页面，缺失遥测对应的卡片、行和轮换页均不出现。如果过滤后没有
可用的配置页，则安全显示 `overview`。当前设备没有存储温度遥测，所以 `storage` 可配置但
会在自动轮换中跳过：

```sh
e87nctl display rotation on
e87nctl display rotation-seconds 3
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display config
```

`refresh_seconds` 是数据刷新周期，控制同一页面重新采样的频率；`rotation_seconds` 是
页面切换周期，控制开启轮换后多久换到下一页。两者可以不同，例如数据每 2 秒刷新、页面
每 3 秒切换。轮换关闭时，`screen` 决定配置的固定页面；该页暂时缺少必要遥测时显示
`overview`，数据恢复后自动回到配置的固定页面，不需要重新执行 `display screen`。

Thermal/Fan 页面把“风扇设置”表达为内核当前控制事实：自动模式、cooling level、PWM
百分比和 policy。当前 Debian 实现仍由 `pwm-fan + thermal governor` 负责长期控制；
`e87nctl fan test LEVEL SECONDS` 只进行最多 30 秒的临时测试并恢复原状态，不安装第二个
用户态风扇守护进程，也不在屏幕上伪造 RPM。

## 参考 OpenWrt 设置的落地边界

原项目的亮度、开关、页面和定时策略可以作为配置体验参考。Debian 侧目前稳定保留：

- `/etc/e87n/display.json`：亮度、当前页面、主题、刷新周期和轮换页面列表；
- `e87nctl display on|off|brightness|screen|theme|refresh|rotation|rotation-seconds|pages`；
- 背光 active-low 映射和启动时重新应用保存值；
- framebuffer 消失或服务异常时由 systemd 重启服务；
- 风扇配置由内核 thermal governor 统一仲裁，显示服务只读温度/档位/PWM。

原 OpenWrt 的静音、均衡、性能、自定义曲线以及亮度/开关时间表暂不直接移植成第二套
控制器。若后续增加这些配置，必须复用同一个 kernel/user-space 仲裁点，并保留温度读取失败
时的全速保护；不能让显示服务或独立脚本与内核 governor 争抢 PWM。

## Image2.5 视觉稿提示词

下面的提示词可用于 Image2.5 生成视觉探索图；生产屏幕仍以代码渲染为准：

```text
UI mockup, exact 428x142 ultra-wide industrial embedded-device LCD dashboard,
dark navy background, cyan accent, green normal state, amber temperature warning,
crisp high-contrast RGB565-safe flat panels, no gradients, no shadows, no tiny icons.
Top header: "E87N / SYSTEM" and uptime. Middle: one full-width active LAN card
labeled "LAN 1" with link state and IP address; do not draw an empty second card.
Bottom: CPU usage,
RAM usage, CPU temperature, fan mode/cooling level/PWM percentage.
Show "自动 75%" for the fan. Do not show RPM, tachometer, speed claims,
Chinese paragraphs, logos, watermarks, browser chrome, or desktop-sized UI.
All text must stay inside the 428x142 canvas and remain legible at native size.
```

## 实机验收

预览图只能验证布局，不能证明屏幕已点亮。目标板上仍需逐项检查：

```sh
e87nctl display config
e87nctl display screen overview
e87nctl display brightness 20
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager
```

并实测 0/20/100% 亮度、开关持久化、网口动态隐藏/全宽布局、温度读数和风扇真实起转。

## 2026-09-21 真实板卡验收

`e87n-display 1.3.1-1` 已安装到 `192.168.20.201`。实机快照中 `eth0` 在线，`eth1` 为
`carrier=0` 且仍有 link-local IPv6 和历史计数；实际渲染只保留全宽 `网口1 / eth0`。
风扇页不显示内部值 `kernel-thermal`。当前没有存储温度，因此 7 个可用页面在三主题下共
21 次从真实 `/dev/fb0` 读回完整且不同的帧；固定 `storage` 正确回退 `overview`。现场操作
者随后确认实体屏幕显示正常。该确认没有附带照片或仪器级颜色、亮度测量；详细哈希、测试
数量和边界见 [真实板卡验收记录](FINAL-VALIDATION-20260921.md)。

### 历史证据

下文保留旧版本当日验收事实；`1.3.0-1` 及更早版本的固定双网口行为不代表当前设计。

设备 `192.168.20.201` 已在 Debian 13 Trixie / Linux `6.18.52-current-edgepi-e87n` 上
安装并运行历史验收版本 `e87n-display 1.1.5-1`。目标板实测 framebuffer 为 `fb_nv3007`、`428×142`、
RGB565，`fonts-wqy-microhei` 已安装；`eth0`、`eth1` 两个网口均能被采样，`eth0` 的 IPv4
为 `192.168.20.201`，`eth1` 无 IPv4 但有真实 IPv6 链路地址。

本轮修复了两个实机问题：兼容 fbtft 返回的 `nonstd=1` 标记，并给显示服务增加仅用于
本地 `SIOCGIFADDR` 的 `AF_INET` 权限，避免 systemd 沙箱导致 IPv4 采样失败后错误显示链路本地
IPv6。修复后的服务连续检查为 `active`、`NRestarts=0`、`ExecMainStatus=0`。

历史 `1.2.0-1` 的四个页面均从真实 `/dev/fb0` 读回并确认尺寸为 `428×142`：`overview`、
`thermal`、`network`、`storage`。真实风扇状态为 `pwm-fan`、`step_wise`、`L1/3`、PWM `50%`；板上没有可靠的
tachometer 输入，因此不显示 RPM。`e87nctl display off/on`、亮度 20%、页面切换和
`e87nctl fan test 1 1` 均已完成，风扇测试恢复原档位。

以上是历史版本的软件、framebuffer 写入和真实遥测验收；由于当前环境没有可用摄像头，尚未把 LCD 面板
本身的肉眼观感写成“已拍照确认”。
