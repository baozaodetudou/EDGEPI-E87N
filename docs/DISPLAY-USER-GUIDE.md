# E87N 屏幕包更新与设置指南

本文面向已经启动 E87N Debian 系统、希望单独更新或调整小屏的用户。屏幕程序由
`e87n-display` Debian 包提供，服务名为 `e87n-display.service`，配置保存在
`/etc/e87n/display.json`。

## 先分清屏幕包和整机固件

| 文件 | 用途 | 在哪里操作 |
| --- | --- | --- |
| `e87n-display_<version>_all.deb` | 单独更新小屏程序和服务 | 已启动的 Debian 中用 APT 安装 |
| `edgepi-e87n-debian_<version>_arm64-uboot-firmware.tar` | 安装或升级整套 Debian 系统 | 原厂 U-Boot Web 的 firmware 入口 |

屏幕 `.deb` 不包含 U-Boot、内核、DTB 或 rootfs，不能上传到 U-Boot 页面。固件 TAR 也不能
用 APT 安装。两种 Release 的版本和发布日期可以不同；只更新小屏时不需要重新刷机。

## 更新屏幕包

### 1. 下载并校验

从仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 打开目标
**Display Release**，只下载 `e87n-display_<version>_all.deb`。把命令中的 `<version>` 和
`<设备IP>` 换成实际值。

Linux 校验：

```sh
sha256sum e87n-display_<version>_all.deb
```

macOS 校验：

```sh
shasum -a 256 e87n-display_<version>_all.deb
```

输出必须与该 Display Release 正文中的 SHA-256 完全一致。不要拿 Firmware Release 的摘要
校验屏幕包；摘要不一致、文件大小为 0 或下载不完整时不要安装。

### 2. 上传到设备

```sh
scp e87n-display_<version>_all.deb root@<设备IP>:/tmp/
ssh root@<设备IP>
```

除 `e87nctl display config` 和帮助命令外，安装及显示写命令都需要 root。非 root 用户应在
命令前加 `sudo`。

### 3. 备份当前设置

```sh
mkdir -p /root/e87n-display-backup
installed_version=$(dpkg-query -W -f='${Version}' e87n-display 2>/dev/null || printf uninstalled)
cp -a /etc/e87n/display.json "/root/e87n-display-backup/display.json.${installed_version}"
e87nctl display config
```

备份文件名会记录升级前的包版本。`e87nctl display config` 只读取并校验配置，不修改屏幕。

### 4. 检查并安装

先确认文件确实是目标包：

```sh
dpkg-deb -f /tmp/e87n-display_<version>_all.deb Package Version Architecture
```

应看到包名 `e87n-display`、目标版本和架构 `all`。然后安装或升级，并在无人值守安装时明确
保留当前的 `/etc/e87n/display.json`：

```sh
apt-get -y \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold \
  install /tmp/e87n-display_<version>_all.deb
```

如果 APT 报告依赖无法取得，先执行 `apt-get update`，确认设备能够访问 Debian 软件源，再
重新执行安装命令。包的安装脚本会刷新 systemd 并尝试启动或重启显示服务，正常升级不需要
手工重启。

### 5. 验证升级结果

```sh
dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
systemctl is-enabled e87n-display.service
systemctl is-active e87n-display.service
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
e87nctl display config
```

正常结果应包含：

- `e87n-display <version> install ok installed`
- `enabled`
- `active`
- `NRestarts=0`
- `ExecMainStatus=0`

## 常用设置命令

| 目的 | 命令 | 取值 |
| --- | --- | --- |
| 查看当前配置 | `e87nctl display config` | 只读，不修改配置 |
| 固定页面 | `e87nctl display screen <page>` | 八个页面之一 |
| 切换主题 | `e87nctl display theme <theme>` | `dark`、`aurora`、`light` |
| 设置亮度 | `e87nctl display brightness <percent>` | 整数 `0..100` |
| 设置刷新周期 | `e87nctl display refresh <seconds>` | 整数 `2..60` 秒 |
| 开关自动轮换 | `e87nctl display rotation on\|off` | 开启或关闭 |
| 设置轮换周期 | `e87nctl display rotation-seconds <seconds>` | 整数 `2..60` 秒 |
| 设置轮换页面 | `e87nctl display pages <page,...>` | 1–8 个不重复页面 |
| 暂时关屏 | `e87nctl display off` | 保留亮度、主题和页面设置 |
| 恢复显示 | `e87nctl display on` | 使用已保存的亮度 |

每个写命令都会校验配置、原子保存到 `/etc/e87n/display.json`，并立即应用背光状态。显示
服务每轮都会重新读取配置，因此主题、页面和轮换设置通常在当前刷新周期内自动生效，不需要
重启服务。默认刷新周期为 2 秒。

查看完整命令帮助：

```sh
e87nctl display --help
```

### 固定显示一个页面

固定页面前必须关闭自动轮换：

```sh
e87nctl display rotation off
e87nctl display screen overview
```

把 `overview` 换成需要的页面即可。只执行 `display screen` 而不关闭轮换时，这个值会作为
轮换开始页，但屏幕仍会继续切换。

### 自动轮换页面

例如每 5 秒在总览、CPU、内存和网络页之间轮换：

```sh
e87nctl display pages overview,cpu,memory,network
e87nctl display rotation-seconds 5
e87nctl display rotation on
```

页面顺序就是逗号列表的顺序，不能重复，也不能包含空格。要恢复八页轮换：

```sh
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display rotation-seconds 3
e87nctl display rotation on
```

`refresh` 控制同一页面多久重新采样和重绘，`rotation-seconds` 控制多久切换页面，两者互不
替代。例如刷新 2 秒、轮换 5 秒时，当前页面每 2 秒更新一次，每 5 秒切到下一页。

### 切换主题

```sh
e87nctl display theme dark
e87nctl display theme aurora
e87nctl display theme light
```

- `dark`：深色高对比配色。
- `aurora`：近黑背景，洋红/紫色主强调并辅以青色。
- `light`：浅色高对比配色。

主题只改变颜色，不改变页面字段、遥测来源或自动隐藏规则。旧版主题名会在读取配置时迁移：
`dual -> dark`、`single -> light`、`compact -> aurora`。

### 亮度、关屏和开屏

```sh
e87nctl display brightness 20
e87nctl display off
e87nctl display on
```

`brightness 0` 与 `display off` 不完全相同：

- `brightness 0` 保存 0% 亮度，但显示守护程序仍采集数据并绘制 framebuffer。
- `display off` 保存 `enabled=false`，关闭背光，并使显示守护程序跳过数据采样和绘制。
- `display on` 恢复采样和绘制，并使用已保存亮度；如果保存的亮度是 0%，开屏后仍然是黑的，
  需要再执行 `e87nctl display brightness 20` 或其他非零值。

关屏不会停止风扇，也不会禁用 Linux thermal governor。

## 页面内容

| 页面 | 显示内容 |
| --- | --- |
| `overview` | 系统总览、有效网口和当前可用的健康状态 |
| `cpu` | CPU 使用率、负载、当前频率、governor 和驱动 |
| `memory` | 内存使用量、可用量和占用比例 |
| `thermal` | 当前可读取的 CPU/PHY 温度和风扇状态 |
| `fan` | 内核风扇模式、cooling level 和 PWM 百分比；不显示虚假的 RPM 或内部控制字符串 |
| `network` | 有效网口的链路、地址和 RX/TX 累计计数 |
| `traffic` | 所有可见网口的 RX/TX 累计总量、主链路和本地地址；不显示瞬时速率 |
| `storage` | 可读取的 NVMe 温度 |

## 无数据时为什么会自动隐藏

显示程序不会为不存在的数据保留空卡片：

- 缺少某项遥测时，对应卡片或行自动隐藏。
- 自动轮换会跳过当前没有有效数据的页面。
- 固定页面暂时不可用时先显示 `overview`，数据恢复后自动回到原固定页面。
- 网口 `carrier=0` 时，即使还残留 IP 地址或 RX/TX 计数也会隐藏。
- carrier 未知时，只有有效 IPv4 或全局 IPv6 才显示；只有 link-local IPv6 不显示。
- 只剩一个有效网口时，该网口卡片自动占满整行。
- 当前已验证设备没有可用存储温度，因此 `storage` 可配置，但通常会被自动跳过。

这些行为与主题无关。页面没有出现时，应先确认对应硬件遥测是否存在，不要先反复重装包。

## 配置保存与恢复

配置文件包含以下 8 个字段：

| 字段 | 合法值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` / `false` | 是否启用采样、绘制和背光 |
| `brightness_percent` | `0..100` | 保存的亮度百分比 |
| `screen` | 八个页面之一 | 固定页或轮换开始页 |
| `refresh_seconds` | `2..60` | 数据刷新周期 |
| `theme` | `dark` / `aurora` / `light` | 配色主题 |
| `rotation_enabled` | `true` / `false` | 是否自动轮换 |
| `rotation_seconds` | `2..60` | 页面切换周期 |
| `rotation_screens` | 1–8 个不重复页面 | 页面轮换顺序 |

推荐只用 `e87nctl` 修改配置。手工编辑时必须保留全部 8 个键、合法 JSON、root 所有权，且
文件不能被组或其他用户写入，也不能是符号链接。编辑后先验证：

```sh
e87nctl display config
```

恢复备份时：

```sh
cp -a /root/e87n-display-backup/display.json.<备份版本> /etc/e87n/display.json
e87nctl display config
systemctl restart e87n-display.service
```

`e87nctl display apply` 是 systemd 的启动前应用命令，普通设置不需要手工执行。

## 回退到旧版

必须事先保留并校验旧版 `.deb`，同时准备**目标旧版本仍能识别**的配置备份。不要把 1.3
的八页面、`dark/aurora/light` 配置直接保留给只认识四页面和旧主题名的 1.2 包，否则旧服务
会因配置校验失败而无法启动。

先恢复在目标旧版本运行时保存的配置，再执行回退：

```sh
cp -a /root/e87n-display-backup/display.json.<旧版本> /etc/e87n/display.json
e87nctl display config

apt-get -y --allow-downgrades \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold \
  install /tmp/e87n-display_<旧版本>_all.deb

dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
systemctl is-active e87n-display.service
```

回退只影响显示程序，不会回退内核或整机系统。如果没有与目标旧版本匹配的配置备份，应先
查阅该版本文档并生成合法配置；不要盲目降级后再排查黑屏。

## 故障排查

### 屏幕不亮

```sh
e87nctl display config
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager -n 100
cat /sys/class/graphics/fb0/name
ls -l /dev/fb0 /sys/class/backlight/backlight
```

依次确认：

1. 配置中的 `enabled` 是 `true`。
2. `brightness_percent` 大于 0。
3. 服务状态是 `active`。
4. `/sys/class/graphics/fb0/name` 是 `fb_nv3007`。
5. journal 中没有配置、framebuffer、字体或背光错误。

如果配置正确但需要重新初始化 framebuffer，再执行一次：

```sh
systemctl restart e87n-display.service
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
```

### 配置命令报错

```sh
e87nctl display config
journalctl -u e87n-display.service -b --no-pager -n 100
```

常见原因是手工编辑后缺少字段、JSON 格式错误、页面/主题名称无效、权限过宽或配置文件是
符号链接。先修复配置，再重启服务。不要通过删除字段绕过校验。

### 页面没有轮换或某页不出现

```sh
e87nctl display config
systemctl is-active e87n-display.service
```

确认 `rotation_enabled` 为 `true`、`rotation_screens` 包含目标页面、轮换间隔合法。配置正确
但页面仍被跳过，通常表示该页面需要的遥测当前不可用；这是预期行为。

### 服务反复重启

```sh
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
journalctl -u e87n-display.service -b --no-pager -n 200
```

先按日志修复配置或硬件节点。反复重新安装 `.deb` 不能修复缺失的内核、DTB、`/dev/fb0`
或背光节点。

## 停用、卸载和清理

只想暂时关屏时使用：

```sh
e87nctl display off
```

不再需要显示程序时：

```sh
e87nctl display off
apt-get remove e87n-display
```

先执行 `display off` 才能明确关闭背光。单独停止服务或直接 `remove` 不负责清空 framebuffer，
物理屏幕可能保留最后一帧和背光。`remove` 会停止服务并保留 dpkg conffile，便于以后重新
安装。重新安装后配置仍是关屏状态，需要执行 `e87nctl display on`。只有确定要删除包配置时
才使用：

```sh
apt-get purge e87n-display
```

卸载显示包不会卸载内核显示驱动，也不会改变由内核管理的风扇温控。

包构建、维护脚本、chroot 预装和开发测试细节见[独立屏幕包技术说明](DISPLAY-PACKAGE.md)；
屏幕布局和字段设计见[屏幕设计与功能说明](SCREEN-DESIGN.md)。
