# 最小系统默认配置

本配置仅用于本仓库新构建的 Debian 13 Trixie 显示/风扇最小镜像，不修改设备现有 OpenWrt。版本唯一来源为 [e87n-build.json](../userpatches/config/e87n-build.json)：当前审查目标为 Debian 13.7、Linux 6.18.52 LTS，包内核 release 为 `6.18.52-current-edgepi-e87n`。历史同名镜像不会自动更新。

| 项目 | 默认值 |
| --- | --- |
| 登录 | 用户 `root`，密码 `doumao`；SSH 22 端口允许 root 密码登录 |
| 有线网络 | 两个网口默认 DHCP；networkd + netplan；无固定管理 IP |
| DNS / 时间同步 | systemd-resolved / systemd-timesyncd |
| 时区 | `Asia/Shanghai` |
| 语言与编码 | `zh_CN.UTF-8`；`LANGUAGE=zh_CN:zh` |
| 软件管理 | Debian 签名软件源，支持 `apt update`、`apt install` |
| 小屏 | 预装 `e87n-display`；LCD/背光节点启用，配置 `fb_nv3007` 自动加载及显示服务；独立 deb 用于升级 |
| 风扇 | 内核自动温控；没有第二个用户态风扇控制器 |

默认密码是公开的，只在可信内网首启，登录后运行 `passwd` 改密。没有首次创建用户的向导或强制公钥门槛；串口也不自动免密登录。镜像不携带共用 SSH host keys，首次启动在 SSH 前生成设备自己的密钥。

从路由器 DHCP 租约获取实际 IP 后：

```sh
ssh root@"<设备IP>"
passwd
apt update
apt install --no-install-recommends curl
```

新镜像通过 APT 预装版本化 `e87n-display`，包含 `e87nctl`、默认显示配置和
`e87n-display.service`；下一次启动由模块配置加载 `fb_nv3007`。默认 overview 页面、
20% 亮度、每 2 秒刷新、默认 `dual` 双网口主题，页面轮换默认关闭。当前构建不安装旧 `e87n-headless.conf` 黑名单；
仓库保留的 headless 文件及审计选项用于旧配置，不是当前默认。
温度与风扇由内核管理，显示服务不写风扇控制节点。

GitHub Actions 会对当前 main 重新构建预装显示配置，并把同一版本的 `e87n-display` 作为
独立附件发布。QEMU 能验证服务、依赖和包生命周期；真实屏幕亮度、双网口协商速率、温度
精度和风扇散热仍应在目标板上复核。

不预装桌面、Web 管理后台、Docker、LuCI 或额外 RAID/LVM 管理套件；没有 DHCP 服务器、NAT、LAN/WAN 角色划分。根据用途再安装软件，避免镜像承担未使用的后台服务。内核仍保留正常 Linux 底层和板级驱动；额外存储模块为[可选构建项](OPTIONAL-STORAGE.md)。

小屏独立升级见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)，云端构建见 [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md)。
小屏不显示 RPM；风扇仅显示内核自动模式、cooling level 和 PWM 百分比。RPM 需要硬件测速反馈，
原机未提供该反馈，不能把 PWM 或 cooling level 换算成转速。

小屏主题与页面轮换：`dual` 适合双网口，`single` 适合只使用一个网口，`compact` 用于
一屏显示更多摘要。使用 `e87nctl display theme dual|single|compact` 切换主题，使用
`e87nctl display rotation on` 和 `e87nctl display pages overview,network,thermal,storage`
启用页面轮换；轮换时间由 `e87nctl display rotation-seconds 3` 设置。

为防止通用 Filogic 包替换 E87N 移植，保留内核/DTB/BSP 等 Armbian hold。普通 Debian 软件正常更新；解除 hold 或更新内核前需要重新移植并验证。没有自动重启策略。

参考了 [OPhub 中文说明](https://github.com/ophub/amlogic-s9xxx-armbian/blob/main/README.cn.md) 的 DHCP、SSH 与命令行使用体验，不使用其其他 SoC 的内核、DTB、U-Boot 或 eMMC 安装器。
