# E87N 小白刷机与首次启动指南

这份指南只针对本项目发布的 E87N Debian 13 / Armbian 固件。第一次操作时请完整读完，
不要边猜边刷。刷机有写坏系统和丢失原系统数据的风险；正式设备应先做可恢复的 eMMC
备份，并确认 RESET 和原厂 U-Boot Web 页面可以再次进入。

## 你需要准备什么

- 一台电脑，最好使用有线网卡；关闭电脑上的 VPN、代理和其他会抢路由的网络工具。
- E87N 电源和一根能传输网络数据的网线。
- 一个普通路由器或交换机，用于刷完后给 Debian 分配 DHCP 地址。
- Firmware Release 中的 `*-uboot-firmware.tar`：刷入系统必须使用的唯一项目附件。
- 可选：Display Release 中的 `e87n-display_<version>_all.deb`。固件已经预装显示基线包，
  只有需要独立升级小屏程序时才下载 deb。

不要下载 `Source code.zip`，不要把 `.deb` 当固件，也不要把 `.img`、`.img.xz` 或
`*-uboot-firmware.tar` 上传到 LuCI 的 OpenWrt `sysupgrade` 页面。本项目发布的 TAR 是给
原厂 U-Boot Web 的 plain firmware / `firmware` 入口使用的。

## 第一步：下载并校验固件

从仓库的 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases) 打开目标
Firmware Release，只下载 `*-uboot-firmware.tar`。该 Release 正文会列出固件 SHA-256：

Linux：

```sh
sha256sum <固件文件>.tar
```

macOS：

```sh
shasum -a 256 <固件文件>.tar
```

摘要不一致、文件大小为 0 或下载未完成时，停止操作，不要上传。

## 第二步：让设备进入 U-Boot

刷 U-Boot 时只先连接一个网口，另一个网口先拔掉，避免电脑接错接口。步骤如下：

1. 给 E87N 断电，等待几秒。
2. 用手按住设备的 `RESET` 键，不要松开。
3. 按住 RESET 的同时给设备上电。
4. 继续按住约 3–8 秒后松开，等待 U-Boot 网络服务启动。
5. 用网线把电脑连接到设备当前使用的网口。
6. 给电脑这块有线网卡临时设置静态地址：
   - IP：`192.168.1.2`
   - 子网掩码：`255.255.255.0` 或 `/24`
   - 网关和 DNS：留空
7. 浏览器打开 `http://192.168.1.1/`。

能看到 U-Boot 页面，才说明进入成功。这个地址是 U-Boot 的临时地址，不是 Debian 启动
后的固定地址。

### Windows 设置静态地址

打开“设置 → 网络和 Internet → 高级网络设置 → 更多网络适配器选项”，找到连接 E87N
的“以太网”，打开“属性 → Internet 协议版本 4 (TCP/IPv4)”，选择“使用下面的 IP 地址”，
填写 `192.168.1.2` 和 `255.255.255.0`，网关留空。

### macOS 设置静态地址

打开“系统设置 → 网络 → 以太网 → 详细信息 → TCP/IP”，将“配置 IPv4”改为“手动”，
IP 填 `192.168.1.2`，子网掩码填 `255.255.255.0`，路由器留空。

### 打不开 `192.168.1.1` 时

按下面顺序排查：

1. 确认设备确实断电后按住 RESET 再上电，而不是系统已经启动后才按 RESET。
2. 确认网线插在设备的网口，电脑显示“以太网已连接”。
3. 暂时关闭 Wi-Fi、VPN 和其他有线网卡，避免浏览器走错接口。
4. 确认电脑地址是 `192.168.1.2`，不是自动获取的 `169.254.x.x`。
5. 换另一个物理网口重新执行一次；每次只接一个口。
6. 仍然没有页面时，停止刷写，记录设备 LED、网口灯和 RESET 时机，进入项目的详细
   [首启准备与验收说明](first-boot.md)排查。

## 第三步：在 U-Boot 页面上传固件

页面名称可能因厂商 U-Boot 版本略有不同，但要找的是 `firmware`、`plain firmware` 或
类似的固件上传入口：

1. 点击固件上传入口。
2. 选择 Release 下载的 `*-uboot-firmware.tar`。
3. 点击上传/升级按钮一次。
4. 等待页面显示成功或完成；期间不要断电、刷新页面、拔网线或重复点击。
5. 完成后按页面提示重启；如果页面没有提示，先断电等待几秒，再重新上电。

不要在这里上传：

- `e87n-display_*.deb`；
- `.img` / `.img.xz`；
- `kernel`、`root`、`CONTROL` 等 TAR 内的单个文件；
- LuCI 的 `sysupgrade` 入口。

本项目 TAR 内部虽然使用 `sysupgrade-edgepi` 目录名，是为了兼容原厂解析器，不代表它
是 OpenWrt 系统或可以交给 LuCI。

## 第四步：刷完后第一次启动 Debian

1. 让设备重新接到普通路由器/交换机，不再使用电脑的 `192.168.1.2` 静态地址。
2. 第一次启动只接一个网口，等待约 1–3 分钟；不要马上重复断电。
3. 在路由器后台查看 DHCP 租约，找到新设备的 IP。Debian 默认 DHCP，没有固定管理 IP，
   也不会固定使用 `192.168.1.1`。
4. 从电脑连接：

```sh
ssh root@<路由器分配给设备的IP>
```

默认密码是：`doumao`。第一次登录后立即改密码：

```sh
passwd
systemctl is-system-running
ip -br addr
apt update
```

如果 `apt update` 正常完成，说明 Debian 的网络、DNS 和 APT 基础功能已经工作。默认时区
是 `Asia/Shanghai`，默认语言/编码为 `zh_CN.UTF-8`。

## 第五步：确认风扇和小屏

```sh
e87nctl doctor
e87nctl fan status
e87nctl display config
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager
```

风扇由 Linux 内核 `pwm-fan` 和 thermal governor 自动控制。屏幕只读取模式、cooling level
和 PWM 百分比，不显示没有硬件依据的 RPM；PWM 百分比也不等于真实转速。

## 第六步：切换屏幕主题和自动轮换

### 三种主题

```sh
e87nctl display theme dark
e87nctl display theme aurora
e87nctl display theme light
```

- `dark`：深色高对比配色。
- `aurora`：近黑背景，洋红/紫色主强调并辅以青色。
- `light`：浅色配色。

三种主题只改变颜色，不改变字段含义或数据可用性规则；页面布局会根据当前有效遥测自动
收缩。保留的旧配置会自动迁移：`dual -> dark`、`single -> light`、`compact -> aurora`。

### 八个页面

```text
overview   总览
cpu        CPU 使用率、负载和频率
memory     内存占用和容量
thermal    温度与风扇
fan        风扇控制状态
network    网口、地址和链路
traffic    RX/TX 累计流量汇总
storage    存储温度
```

固定显示某一页：

```sh
e87nctl display rotation off
e87nctl display screen overview
```

每 3 秒自动轮换：

```sh
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display rotation-seconds 3
e87nctl display rotation on
```

`refresh_seconds` 是数据刷新间隔，`rotation_seconds` 是页面切换间隔，两者互不冲突。轮换
默认关闭。全部八页都可以配置；缺少遥测时，对应卡片、行和自动轮换页面会隐藏。固定选择
的页面暂时不可用时，屏幕先显示 `overview`，数据恢复后自动回到配置页面。当前设备没有
存储温度遥测，所以 `storage` 会自动跳过。升级旧版本时缺少的新字段会自动使用默认值。

网口也会按实时状态收缩：`carrier=0` 时即使还残留地址或流量计数也隐藏；carrier 未知时
必须有有效 IPv4 或全局 IPv6 才显示，只有 link-local IPv6 不够。只显示一个网口时，卡片
自动占满整行。

## 单独升级小屏包

不想重新刷整机时，从目标 Display Release 下载并按该 Release 正文校验
`e87n-display_<version>_all.deb`，然后只升级 `.deb`：

```sh
sha256sum e87n-display_<version>_all.deb
scp e87n-display_<version>_all.deb root@<设备IP>:/tmp/
ssh root@<设备IP>
dpkg-deb -f /tmp/e87n-display_<version>_all.deb Package Version Architecture
apt-get -y \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold \
  install /tmp/e87n-display_<version>_all.deb
dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
systemctl is-active e87n-display.service
```

升级屏幕包不会替换内核、DTB、U-Boot 或 Debian rootfs，也不要求 display 的版本、tag 或
发布日期与当前 firmware 相同；上面的 `--force-confdef --force-confold` 会在无人值守升级时
保留你已有的 `/etc/e87n/display.json`。

完整的下载校验、配置备份、更新、切换、回退和排障步骤见
[屏幕包更新与设置指南](DISPLAY-USER-GUIDE.md)；包构建和维护脚本细节见
[独立屏幕包技术说明](DISPLAY-PACKAGE.md)。不要把 `.deb` 上传到 U-Boot 页面；它只能在
已经启动的 Debian 中由 APT/dpkg 安装。

升级后可直接修改屏幕，不需要重新刷机：

```sh
# 主题：dark / aurora / light
e87nctl display theme aurora

# 固定页面：8 个页面任选其一
e87nctl display rotation off
e87nctl display screen overview

# 每 3 秒按顺序轮换八页
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display rotation-seconds 3
e87nctl display rotation on

# 亮度和刷新频率
e87nctl display brightness 20
e87nctl display refresh 2

# 查看配置并确认服务
e87nctl display config
systemctl is-active e87n-display.service
```

`e87nctl` 会校验并安全保存配置到 `/etc/e87n/display.json`。一般不需要重启服务；如果
需要立即重新初始化 framebuffer，可手动执行：

```sh
systemctl restart e87n-display.service
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
```

## 刷写失败或系统不启动怎么办

1. 不要连续重复上传未知文件，也不要把 `.img` 写进 U-Boot 的错误入口。
2. 断电，按住 RESET，再上电，重新打开 `http://192.168.1.1/`。
3. 只用原 Firmware Release 的 `*-uboot-firmware.tar` 重试，并再次校验该 Release 的 SHA-256。
4. 记录 U-Boot 页面上的错误文字、上传阶段、设备 LED 和连接的网口。
5. 如果 U-Boot 也进不去，停止操作，保留串口/网络日志，按[详细恢复边界](first-boot.md)
   处理；不要自行擦除 GPT、FIP、boot0/boot1 或 U-Boot 环境。

刷机前推荐保存原系统的完整 eMMC 备份。没有备份时，任何“重新刷一次”都不能保证恢复原
系统数据。

## 重要提醒

- 本项目是 Debian/Armbian，不是 OpenWrt；正常服务由 systemd 管理，SSH 服务名称是
  `ssh`/`sshd`，屏幕服务是 `e87n-display.service`。
- U-Boot 的 `192.168.1.1` 只在恢复页面阶段有效；Linux 启动后必须从路由器 DHCP 租约找 IP。
- 默认密码公开，只适合可信内网首次登录；改密码前不要把设备直接暴露到公网。
- 软件测试、QEMU 测试和 Release 摘要不能替代每台实机的显示、散热和断电恢复验收。
