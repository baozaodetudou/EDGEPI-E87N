# E87N 小屏设计与功能说明

## 设计目标

E87N 使用 428×142 的超宽低高度 NV3007 LCD。界面优先级是：确认设备健康状态、区分两个
物理网口、用大字号显示关键数值，并对缺失数据明确显示 `--` 或 `NO IP`。

## 双网口主页面

推荐布局：

```text
┌────────┬──────────────────────┬──────────────────────┬──────────────┐
│ E87N   │ LAN 1  ● UP  2.5G    │ LAN 2  ● UP  1G      │ CPU  24%     │
│ 2d 04h │ 192.168.20.201       │ 192.168.20.202       │ RAM  38%     │
│        │ RX 1.2G  TX 340M     │ RX 860M   TX 120M    │ TEMP 57°C    │
│        │                      │                      │ FAN  AUTO    │
└────────┴──────────────────────┴──────────────────────┴──────────────┘
```

两个网口必须使用固定位置和固定标签 `LAN 1`、`LAN 2`，不能按照当前活动接口重新排序，
否则用户无法长期对应物理端口。IPv4、IPv6、接口名和速率必须动态缩放或截断，不能溢出。

## 状态规则

| 状态 | 显示 | 含义 |
| --- | --- | --- |
| 已连接且有地址 | `● UP`、速率、IP | carrier up，显示实际本地地址 |
| 已连接但未获取地址 | `● UP`、`NO IP` | 链路存在，但 DHCP/静态配置尚未提供地址 |
| 未连接 | `○ DOWN`、`LINK OFF` | 物理链路未建立 |
| 数据暂不可读 | `--` | 不把缺失值当作 0 或 DOWN |
| 温度过高 | 数值 + 警示色 | 只提示，不改变内核散热策略 |

颜色建议为深海军蓝背景、青色主数值、绿色正常、橙色警告、灰色缺失。RGB565 下不使用
细碎渐变和复杂阴影；所有文字在低亮度和灰度下仍需可读。

## 页面规划

| 页面 | 内容 |
| --- | --- |
| Overview | 两个网口、各自 IP/速率、CPU、内存、温度、风扇策略 |
| Thermal | CPU/PHY 温度、thermal state、PWM/level、实际 RPM |
| Network | 两个接口的 link、IPv4/IPv6、累计 RX/TX 计数 |
| Storage | 可用温度传感器和存储设备状态 |

页面切换、亮度和刷新周期由独立 `e87n-display` 包提供的 CLI 管理；屏幕服务不写风扇
控制节点，内核 thermal governor 仍是风扇唯一控制者。

## 当前实现状态

- 已完成：NV3007 驱动、428×142 RGB565 framebuffer、270° 旋转、PWM 背光、systemd 服务、
  四页面渲染、配置校验和独立 Debian 包。
- 已完成：兼容 fbtft 驱动返回的 `nonstd=1` 标记，并保留严格 RGB565 位域校验。
- 已完成：设备现场服务验证，`e87n-display.service` 稳定运行且重启次数为 0。
- 进行中：将 Overview 从“选择一个主地址”升级为固定的双网口卡片布局。

文档中的双网口布局是下一版 UI 的设计规范；在代码提交完成前，不把它描述成已经发布
到镜像中的最终页面。

## 调试命令

```sh
e87nctl display config
e87nctl display screen overview
e87nctl display screen thermal
e87nctl display screen network
e87nctl display screen storage
e87nctl display brightness 20
e87nctl display refresh 2
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager
```

显示服务启动失败时，先看 framebuffer 和驱动：

```sh
cat /sys/class/graphics/fb0/name
cat /sys/class/graphics/fb0/virtual_size
cat /sys/class/graphics/fb0/bits_per_pixel
e87nctl doctor
```
