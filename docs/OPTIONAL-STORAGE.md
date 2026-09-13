# 可选存储与 VPN 内核扩展

`E87N_EXTRA_STORAGE` 默认是 `no`。只有小写 `yes`、`no` 是合法值；未设置时取 `no`，显式空值、大小写变体和其他值会在内核配置 hook 修改请求前报错。

从仓库根目录构建：

```sh
./build.sh                         # 默认 no
./build.sh E87N_EXTRA_STORAGE=no    # 显式关闭
./build.sh E87N_EXTRA_STORAGE=yes   # 按需加入数据卷/额外 VPN 支持
```

使用命令行参数可让现有启动器将设置传入 Armbian，包括 macOS Docker 构建。该开关作用于后续构建，不会改变已有镜像或正在运行的系统。

| 能力 | 默认 / `no` | `yes` |
| --- | --- | --- |
| device-mapper、dm-crypt、snapshot、thin provisioning、mirror、zero、multipath | 强制禁用 | 模块 |
| Linux 软件 RAID：linear、RAID0/1/10/4/5/6 | 强制禁用 | 模块 |
| `MD`、`DM_UEVENT` | 强制禁用 | 内建 |
| `XFRM_INTERFACE` | 强制禁用 | 模块 |
| `XFRM_USER`、`CRYPTO_XTS`、`CRYPTO_ESSIV` | 保留继承配置 | 明确请求为模块 |
| `MD_AUTODETECT`、`DM_INIT` | 禁用 | 禁用 |

`yes` 沿用原扩展集合，同时明确请求 `MODULES`、`BLOCK`、`NET`、`INET`、`IPV6`、`XFRM`、`CRYPTO` 和 AES/CBC/HMAC/SHA-256/SHA-512 为内建。`no` 不覆盖这些共享依赖的继承值，不会为了关闭存储扩展而禁用通用加密或网络。

默认关闭使用明确的 `opts_n`，并清除 `opts_y` / `opts_m` 中同名的全部冲突请求，包括带 `CONFIG_` 前缀的别名。这样即使种子配置或 family 已启用 DM/软件 RAID，后续配置也会请求禁用；两种模式下重复执行 hook，扩展项都只有一个目标状态。

## 保留的基础能力与范围

ext4 根文件系统、eMMC、串口、看门狗、温控/风扇、屏幕及 PHY 的现有板级策略不变。既有以太网、USB、NVMe、TUN、bridge、nftables 和 WireGuard 配置保留。种子配置已经包含的 `XFRM_USER=m`、`CRYPTO_XTS=m`、`CRYPTO_ESSIV=m` 也保留；默认关闭扩展不等于完全删除 IPsec 或加密能力。

这里仅控制上述新增扩展，不是对整个种子配置做全面裁剪；其他继承驱动（包括种子中的 SCSI 硬件 RAID 驱动）仍沿用原配置。开关不安装软件包、不启用服务，也不调整现有用户空间软件包/服务策略。`yes` 不会配置阵列、解锁数据卷或改变 ext4 根分区，也不会启用内核 RAID 自动发现或命令行 DM 创建设备。

## 验证

```sh
bash tests/test-board-config.sh --hook-only
# Bash 5 + 已有 Armbian checkout：
bash tests/test-board-config.sh /path/to/armbian-build
```

测试覆盖未设置与显式 `no` 等价、重新加载板卡文件保留 `yes`、空请求、重复/别名冲突、真实种子叠加继承启用项、两次 hook 的有效配置一致性，以及非法值报错且不修改数组。同时断言根文件系统和关键网络/存储基线保留。

测试仅检查请求数组及按 Armbian `n → y → m` 顺序叠加后的种子配置，不替代 `olddefconfig` 的依赖解析、内核/镜像构建或板上验证。
