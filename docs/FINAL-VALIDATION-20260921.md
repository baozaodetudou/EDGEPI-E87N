# E87N 真实板卡显示验收（2026-09-21）

本记录对应当日实机上的 `e87n-display 1.1.5-1`。随后新增的 `1.2.0-1` 三种主题、页面
轮换和配置迁移已通过离线/容器测试，但不应把本记录误读为 `1.2.0-1` 已完成上板验收；
新包发布后仍需在真实 E87N 上重新切换主题、开启轮换并观察屏幕。

## 设备与软件

| 项目 | 实测值 |
| --- | --- |
| 设备地址 | `192.168.20.201` |
| 系统 | Debian 13 Trixie / Armbian unofficial |
| 内核 | `6.18.52-current-edgepi-e87n` |
| framebuffer | `fb_nv3007`、`428×142`、16-bit RGB565 |
| 显示包 | `e87n-display 1.1.5-1` |
| 中文字体 | `fonts-wqy-microhei 0.2.0-beta-4` |
| 网口 | `eth0`、`eth1` |

## 结果

- `e87n-display.service`: `active (running)`。
- `NRestarts=0`，`ExecMainStatus=0`。
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

## 验证边界

本记录证明真实板卡上的系统服务、字体、framebuffer 写入、遥测和配置命令可用；当前没有
摄像头或面板光学测量，因此不把“从 framebuffer 读回的图像”描述成 LCD 肉眼拍照验收。
