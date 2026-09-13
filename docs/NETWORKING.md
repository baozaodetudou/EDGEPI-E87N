# E87N 默认 DHCP 与网口身份

默认网络以 [DEFAULTS.md](DEFAULTS.md) 为准：两个有线网口通过 networkd/netplan
请求 DHCP，没有固定管理 IP、DHCP 服务器、LAN/WAN 分工、网桥或 NAT 预设。
当前 `/etc/netplan/10-e87n-dhcp.yaml` 匹配 `e*` 接口，启用 IPv4/IPv6 DHCP 与
IPv6 RA；DNS 使用 systemd-resolved，时间同步使用 systemd-timesyncd。

首次只连接一个网口到可信内网 DHCP 路由器，从租约、小屏或串口读取实际 IP，
然后用 `ssh root@<设备IP>` 连接默认 22 端口，密码 `doumao`，登录后执行 `passwd`。
没有首次创建用户向导或强制公钥门槛；串口需要正常认证。第二个物理网口需单独验收。

## 网口稳定身份：源码补强，实机待测

新增独立补丁 `userpatches/kernel/edgepi-e87n-6.18/901-e87n-ethernet-aliases.patch`，在原 `0000` 创建的板级 DTS 中追加：

```dts
aliases {
    serial0 = &uart0;
    ethernet0 = &gmac0;
    ethernet1 = &gmac1;
};
```

`0000` 本身不修改。此改动为两个 GMAC 补充固定的板级身份，使支持该功能的 systemd 命名方案能够用端口索引生成持久 MAC，减少对 `ethN` 枚举顺序的依赖。**目前只完成源码和离线验证，尚未证明实机重启后的 MAC 稳定。**

当前配方清空 `/etc/machine-id`，沿用 systemd `99-default.link` 的 `MACAddressPolicy=persistent`。DT 别名提供额外身份输入；仓库保留的 `10-e87n-mtk-mac.link` 与默认策略等效，不作为镜像安装项，也没有额外的 MAC 写入程序。新镜像的实际文件与启动行为仍需逐项核对。

## 源码依据与预期效果

1. 固定内核提交 `f6388029ea9e2c9e807d73827658738ea131faee` 的 MTK 驱动按 MAC 节点的 `reg` 选择 netdev，并设置 `eth->netdev[id]->dev.of_node = np`。现有 DTS 的真实标签为 `gmac0: mac@0`、`gmac1: mac@1`，`reg` 分别为 0、1，板级均启用。因此别名指向的是两个不同 MAC 节点，尽管它们共用 `ethernet@15100000` 平台父设备。[固定内核源码](https://github.com/gregkh/linux/blob/f6388029ea9e2c9e807d73827658738ea131faee/drivers/net/ethernet/mediatek/mtk_eth_soc.c)

2. systemd v257 的 `names_devicetree()` 在启用 `NAMING_DEVICETREE_PORT_ALIASES` 时，优先读取 netdev 自己的 `of_node`，再匹配 `/aliases/ethernetN`。上述别名预期得到 `ID_NET_NAME_ONBOARD=end0`、`end1`。这条路径不需要 `ID_NET_NAME_PATH`，也不依赖两个网口可能相同的普通 `ID_PATH`。[net_id 源码](https://github.com/systemd/systemd/blob/v257/src/udev/udev-builtin-net_id.c)

   **需要运行时使用 v257 或包含同一功能标志的命名方案。** 仅安装 systemd 257 并不能排除发行版默认值、`net.naming_scheme` 或 `NET_NAMING_SCHEME` 覆盖成旧方案；旧方案可能只检查共用的父节点，无法利用本补丁。[命名方案标志](https://github.com/systemd/systemd/blob/v257/src/shared/netif-naming-scheme.h)

3. systemd 的持久地址生成优先采用 `ID_NET_NAME_ONBOARD`，与 machine-id 一起哈希。别名生效后，地址身份输入改为 `end0`/`end1`，不再因另一个网卡先注册、导致本口从 `eth0` 变成 `eth2` 而改变。导出的 extlinux 已含 `net.ifnames=0`；该参数只关闭 NamePolicy 改名，仍允许生成身份属性和执行 MAC 策略，主接口名继续沿用内核枚举名，现有 `e*` DHCP 匹配仍适用。别名不会保证物理端口永远叫 `eth0` 或 `eth1`。[身份选择与哈希](https://github.com/systemd/systemd/blob/v257/src/shared/netif-util.c)、[MAC 策略与 net.ifnames](https://github.com/systemd/systemd/blob/v257/src/udev/net/link-config.c)

内核已使用的永久 MAC 仍由默认 persistent 策略保留。生成的地址是本地管理单播地址，**不恢复 factory MAC**。从旧的接口名回退切换为别名身份时，原有回退 MAC 可能改变一次，旧 DHCP 绑定需重新核验。克隆已启动 rootfs 会复制 machine-id；唯一性仍依赖每次安装独立生成并持久化身份。首次 udev 配置网口前必须已有正确身份，initramfs 与 rootfs 的身份/策略时序仍需通过实际启动核对。

`armbian-firstrun` 会替换 `/boot/armbianEnv.txt` 中已有的 `ethaddr`/`eth1addr`，但当前 extlinux/U-Boot 流程没有消费这些值的证据，本方案不依赖该行为。SSH host keys 在首次 SSH 前独立生成，账号默认值和首启边界见 [first-boot.md](first-boot.md)。

## 离线验证与后续实机核对

```sh
python3 -B tests/test-network-policy.py
```

测试从原 `0000` 重建临时 DTS，确认并使用 **GNU patch**（macOS 优先 `gpatch`，Linux 使用 GNU `patch`），以 `--fuzz=0` 应用 `901`，要求无 offset/fuzz；缺少 GNU 实现时直接失败，不使用 Apple patch 替代。验证内容：

- 补丁只增加两条别名，串口别名及其余板级内容不变；
- 文件中部的插入使用标准 3/3 对称上下文，避免 GNU patch 将前置多于后置的 hunk 限制为文件末尾匹配；
- `ethernet0` 精确指向启用的 `gmac0/mac@0/reg=0`，`ethernet1` 指向启用的 `gmac1/mac@1/reg=1`；
- 不存在裸 `ethernet` 与 `ethernet0` 冲突，不添加固定地址；
- 若本机有 `dtc`，用实际别名和 GMAC 声明组成精简 fixture，编译/反编译核对两条别名解析到不同节点；这不是完整 E87N DTB 构建；
- 旧 `10.link` 的配置仍与镜像默认值等效，不把静态测试当作实机 MAC 验证。

当前完整补丁集为 **14** 个。必须使用冻结输入重编真实 DTB/镜像，并对实际 DTB 验证 GMAC aliases；夹具或旧镜像不能作为新构建完成证据。

之后应从真实 DTB 和上板 sysfs/udev 核对两条 alias、netdev 各自的 `of_node`、实际命名方案、`ID_NET_NAME_ONBOARD=end0/end1`、默认 `ID_NET_LINK_FILE` 和 MAC。跨重启及独立初始化的两份系统验证通过前，保留“实机待测”状态。

VM 可丢弃副本的隔离 loopback SSH/PAM 登录已通过，见 [TESTING.md](TESTING.md)；它没有使用 E87N 的物理网口，也不验证 DHCP/DNS、链路吞吐或跨重启 MAC。最小配置保留常规 Linux 网络基础，额外存储开关与管理工具范围见 [OPTIONAL-STORAGE.md](OPTIONAL-STORAGE.md)。
