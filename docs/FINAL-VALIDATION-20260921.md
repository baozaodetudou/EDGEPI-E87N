# E87N 真实板卡显示验收（2026-09-21）

本记录包含同一台真实 E87N 的两次验证：早先的基础 framebuffer 验收使用
`e87n-display 1.1.5-1`；2026 年 9 月 21 日随后将独立包升级到 `1.2.0-1`，并在实体
设备上逐一切换三种主题、开启四页面 3 秒轮换。历史基础结果保留在下文，本文件的最新
结论以 `1.2.0-1` 验收为准。

## 设备与软件

| 项目 | 实测值 |
| --- | --- |
| 设备地址 | `192.168.20.201` |
| 系统 | Debian 13 Trixie / Armbian unofficial |
| 内核 | `6.18.52-current-edgepi-e87n` |
| framebuffer | `fb_nv3007`、`428×142`、16-bit RGB565 |
| 显示包 | `e87n-display 1.2.0-1`（由 `1.1.5-1` 独立升级） |
| 中文字体 | `fonts-wqy-microhei 0.2.0-beta-4` |
| 网口 | `eth0`、`eth1` |

## 结果

- `e87n-display.service`: `active (running)`。
- `NRestarts=0`，`ExecMainStatus=0`。
- `dual`、`single`、`compact` 三种主题均在实体设备上切换，并在每次切换后重启服务确认
  配置加载成功。
- `overview`、`network`、`thermal`、`storage` 四页已配置为每 3 秒轮换；实体 `/dev/fb0`
  连续观察 15 秒期间内容哈希持续变化，服务保持 active 且没有重启。
- 当前最终配置为 `theme=compact`、`rotation_enabled=true`、`rotation_seconds=3`，便于
  继续观察多页面效果。
- `eth0` 显示真实 IPv4 `192.168.20.201`；`eth1` 显示真实 IPv6 链路地址。
- `overview`、`thermal`、`network`、`storage` 四个页面都从真实 `/dev/fb0` 读回，尺寸均为
  `428×142`，中文、颜色和面板边界正常。
- Overview 已确认显示 CPU、内存、温度、风扇 PWM，以及固定的双网口卡片；不显示 RPM。
- Thermal 已确认显示 CPU/PHY 温度、自动模式、cooling level、PWM 和内核策略。
- `e87nctl display off/on`、亮度 20%、页面切换和 `e87nctl fan test 1 1` 通过；风扇测试后
  恢复原档位 `L1/3`。
- 实测风扇：`pwm-fan`、`step_wise`、PWM `128/255 = 50%`，没有 `fan1_input`，因此不把
  PWM 推断为 RPM。

## 本轮修复

1. 接受 E87N fbtft 的 `var_screeninfo.nonstd=1` RGB565 标记。
2. 显示服务的 systemd 沙箱从仅 `AF_UNIX` 调整为 `AF_UNIX AF_INET`，只满足本地
   `SIOCGIFADDR` 读取 IPv4；程序不连接、绑定、发送网络数据，也不打开 AF_INET6。
3. 默认包依赖 `fonts-wqy-microhei`，镜像静态审计和 CI 验证同时检查该字体。
4. 风扇 UI 保留内核 thermal governor、档位和 PWM 百分比，不伪造测速数据。

## `e87n-display 1.2.0-1` 独立升级记录

本次没有刷 U-Boot、内核或整机固件，只在已经启动的 Debian 上上传并安装独立屏幕包：

```text
设备：192.168.20.201
本地构建包：e87n-display_1.2.0-1_all.deb
SHA-256：6f30e49c0e96940dea49ff098ad8d49526bbb35894061d254a0a4302bf6bbf07
```

设备端确认：

```text
e87n-display 1.2.0-1 install ok installed
e87n-display.service enabled / active
NRestarts=0
ExecMainStatus=0
fb_nv3007
```

主题切换使用 `e87nctl display theme dual|single|compact` 完成；轮换使用
`e87nctl display pages overview,network,thermal,storage`、
`e87nctl display rotation-seconds 3` 和 `e87nctl display rotation on` 完成。以后只需升级
`.deb` 或修改 `/etc/e87n/display.json`，不需要重新刷写 U-Boot 固件；操作手册见
[独立屏幕包说明](DISPLAY-PACKAGE.md) 和[小白刷机指南](QUICKSTART-BEGINNER.md)。

## 验证边界

本记录证明真实板卡上的系统服务、字体、framebuffer 写入、遥测、主题切换和页面轮换配置
可用；当前没有摄像头或面板光学测量，因此不把“从 framebuffer 读回的图像”描述成 LCD
肉眼拍照验收。实体设备仍应由操作者观察屏幕颜色、方向、亮度和中文清晰度。
