# E87N 默认 DHCP 与 factory 网口身份

默认网络以 [DEFAULTS.md](DEFAULTS.md) 为准：两个有线网口通过 networkd/netplan 请求 DHCP，没有固定管理 IP、DHCP 服务器、LAN/WAN 分工、网桥或 NAT 预设。`/etc/netplan/10-e87n-dhcp.yaml` 匹配 `e*` 接口，启用 IPv4/IPv6 DHCP 与 IPv6 RA；DNS 使用 systemd-resolved，时间同步使用 systemd-timesyncd。

新 [U-Boot TAR 固件](UBOOT-FIRMWARE.md)保留原 eMMC p2 factory，由首启 helper 在 DHCP 前恢复 MAC。root adapter 24 项、加强后 factory 24 项与完整 Linux regressions 已通过，R4 本地打包/独立静态审计 EXIT 0；它重新打包历史 RAW，没有完整重编内核。**实际首启时序、两个物理端口与跨重启稳定性仍待验收**。V3 已废弃，新增两个编译检查目标的再验证及主机导出复制仍待结果，未来新源码工作流未 dispatch。

原 OpenWrt 已只读确认 eth0/of_node 为 mac0、eth1/of_node 为 mac1。这为当前原系统的端口映射提供证据，不证明新内核枚举、alias 命名或 helper 恢复 MAC 已实测。

仅在[刷写前准备](first-boot.md)全部完成且新系统成功启动后，首次只连接一个网口到可信内网 DHCP 路由器，从租约、小屏或串口读取实际 IP，再用 `ssh root@<设备IP>` 连接默认 22 端口，密码 `doumao`，登录后执行 `passwd`。保存环境中的 `ipaddr=192.168.1.1` 和 `serverip=192.168.1.2` 属于 U-Boot，不是 Debian 固定管理地址。

## DHCP 前的 factory MAC 适配

新 rootfs 适配安装 `/usr/lib/e87n/factory-boot.py`、`e87n-factory-mac.service` 与 networkd 顺序配置。helper 按以下约束工作：

1. 核对原生 aarch64、E87N model/compatible、不可移除 eMMC、p1–p5 的标签/边界以及当前可写 ext4 根设备为 p5。布局来自[只读记录](boot-layout-readonly-20260913.md)，不是对任意分区的通用扫描。
2. 等待 udev，核对 eth0/eth1 的 `of_node` 与 GMAC0/1 aliases，对应关系不能仅凭枚举顺序猜测。已 administratively up 的接口会被拒绝，不能在已运行网络上手动重放该过程。
3. **只读 p2 factory 的 `0x24`、`0x2a` 偏移，各 6 字节**，对应 GMAC0、GMAC1。两个地址必须不同、非零、有效单播；不写 p2，不从 U-Boot 环境读取或保存 MAC。
4. 在 networkd/DHCP 前应用地址，并从 netplan 已生成的 DHCP 配置派生按 alias 匹配的临时 networkd 策略。保留源 YAML 的 `Name=e*` 与 DHCP 设置，增加 `ID_NET_NAME_ONBOARD=end0/end1` 匹配，使 networkd 使用对应 factory 地址。

此实现依赖 `net.ifnames=0`、可用的 GMAC aliases 以及 systemd v257 的设备树端口身份语义；新 FIT bootargs 保留该参数。源码及服务见 [factory-boot](../board-support/factory-boot/README.md)。缺设备、布局不符、无效 MAC 或策略冲突会导致服务明确失败并留下 journal 错误。networkd 使用 `Wants/After`，不是 `Requires`，因此原 DHCP/MAC 回退仍可能启动：**取得 DHCP 租约不证明已采用 factory MAC**。

## GMAC aliases 与历史 persistent 回退

独立补丁 `901-e87n-ethernet-aliases.patch` 为两个 GMAC 提供板级身份：

```dts
aliases {
    serial0 = &uart0;
    ethernet0 = &gmac0;
    ethernet1 = &gmac1;
};
```

固定内核提交 `f6388029ea9e2c9e807d73827658738ea131faee` 的 MTK 驱动按 MAC 节点 `reg` 选择 netdev，并设置其 `dev.of_node`；E87N 中 `gmac0: mac@0`、`gmac1: mac@1` 的 reg 分别为 0、1。两个节点共享平台父设备，但其设备树身份不同。[固定内核源码](https://github.com/gregkh/linux/blob/f6388029ea9e2c9e807d73827658738ea131faee/drivers/net/ethernet/mediatek/mtk_eth_soc.c)

systemd v257 启用 `NAMING_DEVICETREE_PORT_ALIASES` 时按 netdev 的 `of_node` 匹配 `ethernetN`，预期形成 `ID_NET_NAME_ONBOARD=end0/end1`。仅安装版本 257 不排除 `net.naming_scheme` 或 `NET_NAMING_SCHEME` 指定旧方案；运行时仍须核对。[net_id](https://github.com/systemd/systemd/blob/v257/src/udev/udev-builtin-net_id.c)、[命名方案标志](https://github.com/systemd/systemd/blob/v257/src/shared/netif-naming-scheme.h)

历史两分区 GPT 镜像没有 factory 分区，DTS 已移除两个 GMAC 对不存在 provider 的 nvmem 引用，驱动可能使用临时随机 MAC；旧配方依赖 systemd `99-default.link` 的 `MACAddressPolicy=persistent`，结合 alias 与 machine-id 生成本地管理单播地址。那条回退不恢复 factory MAC，不能据此保留“当前没有 MAC helper”的结论。仓库中的 `10-e87n-mtk-mac.link` 是历史配置，不是新 factory 服务。[身份选择与哈希](https://github.com/systemd/systemd/blob/v257/src/shared/netif-util.c)、[MAC 策略](https://github.com/systemd/systemd/blob/v257/src/udev/net/link-config.c)

历史 extlinux 中的 `net.ifnames=0` 只属于中间/旧镜像启动路径；新交付从 FIT 启动。`net.ifnames=0` 关闭 NamePolicy 改名，不保证两个物理端口永远按固定 ethN 编号出现。alias 服务必须验证映射，不能依赖原 `armbianEnv.txt` 中 ethaddr/eth1addr 的替换来完成地址恢复。

machine-id 与 SSH host keys 仍应每台独立生成。DHCP 绑定应核对实际 factory 地址；从旧生成地址迁移后租约可能变化。复制已启动 rootfs 的身份不能作为独立设备配置。

## 验证边界

历史 `tests/test-network-policy.py` 使用 GNU patch 以 `--fuzz=0` 应用 901，检查两条 alias 指向不同的启用 GMAC 节点；有 dtc 时编译精简夹具。这些是 alias 源码验证，既不是完整新 DTB 构建，也不验证新 helper 在真实网络启动时恢复 MAC。

当前 Frank-W 6.18 基线叠加 E87N 补丁，保留 901 GMAC aliases 与 902 内存/保留区修正；补丁列表和摘要以本次构建 receipt 为准。历史 902 dry-run 和 root adapter 24 项测试只对应当时输入，实际新构建仍需审计 FIT 内 DTB 和 root 内 `/boot`。完整上板验收至少需要核对：

- 合法与无效 p2 数据、错误布局、缺 alias/命名属性、已启用接口等情形的明确结果，确保 factory 和环境只读。
- 完整 systemd 启动顺序中 helper 先于 DHCP，最终 MAC 与 p2 两个地址对应，无后续策略覆盖。
- 两个物理端口的链路、PHY 固件、DHCP/DNS/NTP、吞吐及跨重启 MAC；失败回退不能误标为恢复成功。

VM 可丢弃副本的隔离 loopback SSH/PAM 登录已有历史通过记录，见 [TESTING.md](TESTING.md)；它没有使用 E87N 物理网口，不验证 DHCP、factory MAC 或跨重启行为。没有板卡重启、刷写或串口连接记录；必须先完成板上 RAM 测试、恢复备份及其余准备条件。
