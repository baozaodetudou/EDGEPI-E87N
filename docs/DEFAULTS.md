# 最小系统默认配置

本配置仅用于本仓库新构建的 Debian 13 Trixie / Linux 6.18.51 镜像，不修改设备现有 OpenWrt。历史同名镜像不会自动更新。

| 项目 | 默认值 |
| --- | --- |
| 登录 | 用户 `root`，密码 `doumao`；SSH 22 端口允许 root 密码登录 |
| 有线网络 | 两个网口默认 DHCP；networkd + netplan；无固定管理 IP |
| DNS / 时间同步 | systemd-resolved / systemd-timesyncd |
| 时区 | `Asia/Shanghai` |
| 语言与编码 | `zh_CN.UTF-8`；`LANGUAGE=zh_CN:zh` |
| 软件管理 | Debian 签名软件源，支持 `apt update`、`apt install` |
| 小屏 | 预装独立 `e87n-display` 包；总览、20% 亮度、2 秒刷新 |
| 风扇 | 内核自动温控；没有第二个用户态风扇控制器 |

默认密码是公开的，只在可信内网首启，登录后运行 `passwd` 改密。没有首次创建用户的向导或强制公钥门槛；串口也不自动免密登录。镜像不携带共用 SSH host keys，首次启动在 SSH 前生成设备自己的密钥。

从路由器 DHCP 租约或小屏获取实际 IP 后：

```sh
ssh root@<设备IP>
passwd
apt update
apt install --no-install-recommends curl
e87nctl status
e87nctl display screen overview
e87nctl display brightness 20
e87nctl display off
e87nctl display on
```

不预装桌面、Web 管理后台、Docker、LuCI 或额外 RAID/LVM 管理套件；没有 DHCP 服务器、NAT、LAN/WAN 角色划分。根据用途再安装软件，避免镜像承担未使用的后台服务。内核仍保留正常 Linux 底层和板级驱动；额外存储模块为[可选构建项](OPTIONAL-STORAGE.md)。

小屏独立升级见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)，云端构建见 [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md)。显示 RPM 需要硬件测速反馈；原机未提供该反馈，所以显示不可用/PWM，不能虚构转速。

为防止通用 Filogic 包替换 E87N 移植，保留内核/DTB/BSP 等 Armbian hold。普通 Debian 软件正常更新；解除 hold 或更新内核前需要重新移植并验证。没有自动重启策略。

参考了 [OPhub 中文说明](https://github.com/ophub/amlogic-s9xxx-armbian/blob/main/README.cn.md) 的 DHCP、SSH 与命令行使用体验，不使用其其他 SoC 的内核、DTB、U-Boot 或 eMMC 安装器。
