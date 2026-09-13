# E87N Debian 13.6 / Linux 6.18.51 LTS 候选 — 2026-09-12

> 历史候选：此镜像不含后续新增的 NV3007/原生显示程序。当前屏幕/风扇版见 [2026-09-13 记录](candidate-display-fan-20260913.md)；不要把同名旧镜像当作新版。

**状态：完整构建、真实镜像只读静态审计、xz 压缩、Mac 完整导出及本地镜像校验均已完成。** VM 和 Mac 的 `xz --test` 均通过，Mac 压缩文件及流式解压 raw SHA-256 均与 VM 一致。镜像尚未执行、未在 E87N 上启动、未刷写任何设备。此记录不是全驱动、实机或安全生产验收；不要直接接入不可信网络，也不要将整盘镜像直接写入未备份和核对布局的 eMMC。

旧 [Debian 12 Bookworm / Linux 6.12.108 候选](candidate-20260912.md) 的文件和证据原样保留，不覆盖、不重命名，也不借用其结果证明本候选。

## 固定输入与构建完成证据

| 项目 | 本次实际记录 |
| --- | --- |
| 目标系统 | Debian **13.6 Trixie**，ARM64；已核验镜像实际文件系统 |
| 内核 release | `6.18.51-current-filogic` |
| Linux pin | `f6388029ea9e2c9e807d73827658738ea131faee`，官方 Linux 6.18.51 LTS |
| Armbian pin | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| 板级输入 | `edgepi-e87n-6.18` 的 12 个补丁，包含最终 PHY 初始化资源/固件预检修复 |
| 构建选项 | `BSPFREEZE=yes`、`CLEAN_LEVEL=none`，复用此前编译缓存 |
| unit invocation | `282bd69e0665409cb6137de1d30b8046` |
| 完成状态 | `active (exited)`、`Result=success`、`ExecMainCode=1`、`ExecMainStatus=0` |
| 完成时间 | **2026-09-12 23:25:03 CST** |

ARM64 `Image` 于 23:16:03 CST 生成，全部模块编译完成，23:20 进入内核/DTB/headers 包装，随后完成整盘镜像生成。`ExecMainCode=1` 表示正常退出类别，不是进程退出码 1；这里实际退出状态为 0。此前 AppleDouble 输入失败、清洁传输重试和被最终 PHY 输入替代的编译记录见 [构建状态](build-status.md)，不能混同为本次最终产物。

12 个补丁在真实基线上全部通过 `--fuzz=0` 应用。DTB 编译保留 7 条旧节点结构 warning，因此不能称为完整、无告警的 DT schema 校验。

## 已生成产物与校验值

实际 raw 镜像文件名：

```text
Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img
```

| 对象 | 字节数 | SHA-256 |
| --- | ---: | --- |
| raw `.img` | 1,149,239,296 | `1697307756412aef6fa4cabd33bb4c115daa78f80d81e43233a689a3e962f6bc` |
| 压缩 `.img.xz` | 176,462,804 | `32474999d280d4a9057985c6ba6985b1ed5f223584b7f8fe1809f9e4f48cb33e` |
| ARM64 `Image` | 20,103,680 | `8f570946df58492e1bdd082d4613b0f549bfcfa0009b8facd2435c19626bcbe4` |
| 最终安装的 E87N DTB | 21,167 | `99c44a827bd5d27e510043822371e354fcf8bf8c37a6b158164fdbe9a540bec4` |
| 最终 kernel config | 193,658 | `b585649ef53401fbb3290d1e3095a1de4255597bd7b0b6a4d7f3a92efbf39528` |

压缩文件约 168.3 MiB，已在 VM 和 Mac 两端通过 `xz --test`。Mac 压缩文件 SHA-256 与表中值一致；Mac 使用 `xz -dc | shasum -a 256` 得到的 raw SHA-256 也与上表和 VM 重算结果一致。raw 与 `.img.xz` 的校验值不可混用。

实际文件系统 UUID：

| 文件系统 | UUID |
| --- | --- |
| rootfs | `9f2390f1-872e-4a1a-b0a0-a630a2207880` |
| bootfs | `f37789b5-2eb9-48f3-aef1-8c78ac5c4433` |

## 真实只读静态审计范围

整盘 `verify-image --release trixie --require-usb-root` 已成功结束，核验了 GPT、ext4 `fsck -fn`、实际 UUID 与启动配置对应关系、PHY firmware 和 initrd USB-root 驱动链；实际 `.deb` 包及最终安装 DTB/config 验证也通过。检查没有执行目标镜像代码或刷写设备。

最终配置确认 thermal、PWM fan、efuse、USB-root、MMC 所需功能内建；`CPU_FREQ=n`、`CPU_THERMAL=n`、`MEDIATEK_2P5GE_PHY=m`、`MTK_NET_PHYLIB=y`。这不证明真实硬件上的 probe、固件加载、链路、I/O 或散热行为正常。

额外只读检查确认内核、DTB、BSP、base-files 均处于 hold；每个安装包的 `Package`、`Version`、`Armbian-Original-Hash` 与唯一对应的本次 `.deb` 一致。下表记录的是原始 artifact hash，**不是 Debian 的 `Version` 字段，也不是 SHA-256**：

| 安装包/产物 | 已核对的 `Armbian-Original-Hash` |
| --- | --- |
| linux-image / DTB | `6.18.51-Sf638-D0000-P4743-C5fe3-H001f-HK01ba-Vc222-Bc768-R448a` |
| BSP | `1-PCd7ec-V4fe5-H01ba-Bca11-R1b1e` |
| base-files | `1-trixie-1armbian1-Bb095-U13.8--deb13u6-R55fa` |

没有安装 `linux-u-boot` 包；没有发现意外用户 SSH key，shadow 未发现空口令字段。无 U-Boot 包不等于整盘刷写会保留设备启动链；非空口令字段不意味强密码，凭证检查也不是完整安全审计。

## 网络、登录与隔离首启

已从真实镜像文件中确认：

- `10-dhcp-all-interfaces.yaml` 使用 networkd，对 `e*`、`lan*`、`wan*` 请求 IPv4/IPv6 DHCP。这只是配置存在，不证明网口正常或实机已经取得 IP。
- `serial-getty@ttyS0` 已启用，generic getty override 配置了 root autologin；串口为预期首启取证入口，但尚未在板上验证登录。
- 默认凭证为 **`root/1234`**，并允许 SSH root 登录。必须隔离首启并立即改密；shadow 未发现空口令字段不意味强密码或生产安全保证。
- 镜像含有缓存生成的**共享初始 SSH host keys**。首启再生服务使用 `After=ssh.service`，不能假定首次 SSH 连接前已换成唯一密钥。应在隔离环境中完成改密，确认主机密钥已再生并核对指纹后，再开放网络访问。
- root 自动扩容只针对当前根盘；它不负责迁移到另一块盘，也不证明整盘写入、其他盘或原厂分区安全。

首启前先读 [首启验证与写入边界](first-boot.md)，核对原 U-Boot 能力、启动介质和 root UUID，准备串口日志、原启动链备份和恢复方案。静态 extlinux/USB-root 检查不能证明原 U-Boot 支持 extlinux、`booti` 或 USB 加载。没有这些证据前，不向 eMMC 整盘写入此 `.img`。

## 已知限制与待实机项目

- **CPU DVFS / CPU cooling 禁用**，没有 CPU 降频散热；保留固件设定的启动频率，不使用未经证实的 OPP 电压或 `proc-supply`。
- **WED 不支持**，普通 MAC/PHY/PCS 编译成功不代表硬件卸载可用。
- **NV3007 小屏未支持，背光未保证**。vendor fbtft 补丁没有纳入本次或旧 6.12 候选。
- LVTS 使用 1000 ms 普通/passive 软件轮询，不编造物理 IRQ；风扇四级 `<0 128 192 255>` 对应 50/65/75℃ active trips，保留 critical trip，cooling maps 不引用已禁用的 CPU provider。实际温度准确性、风扇起转/转速、负载散热和 critical trip 处置仍待实机。
- **原固定 MAC 尚未恢复**；factory MAC 引用已移除，允许临时随机 MAC，未实现地址持久化，不能依赖旧 DHCP MAC 绑定。
- USB vendor `auto_load_valid` 扩展被 6.18 驱动忽略，不能把保留属性视为 vendor 校准流程已移植。
- U-Boot RAM fixup、最终识别内存、原厂启动能力、网口、eMMC、USB、NVMe、休眠/恢复和重启都仍需实机验证。

## 本地主机交付入口：已完整导出并校验镜像

完整产物已实际到达 Mac，传输校验通过；压缩文件完整性及压缩/解压 SHA-256 均已在 Mac 核验，与 VM 一致。实际 release 目录为：

```text
output/releases/2026-09-12-e87n-trixie-lts-6.18.51/
```

目录相对于原构建工作区，包含同名 `.img.xz` 和 `SHA256SUMS`。它被 Git 忽略，
不是 GitHub 下载地址，也不随克隆获取。当前候选及公开获取状态见 [镜像获取与校验](DOWNLOADS.md)。

本地交付验收已全部完成：

- `.img.xz` 实际存在，Mac `xz --test` 通过；压缩文件 SHA-256 和流式解压 raw SHA-256 均与上表及 VM 结果一致。
- 本机 `xz --robot --list` 确认压缩/解压字节数为 **176,462,804 / 1,149,239,296**，CRC64 正常。
- release 内 `SHA256SUMS` 已实际生成，全部条目通过 `shasum -a 256 -c`。
- host source-build-inputs 的 **52 项全部与 VM manifest 匹配**，确认导出所对应的本次构建输入。

上述入口用于本地取件与校验，不代表已在设备上启动或适合未经核对的整盘刷写。镜像尚未执行或刷写，全驱动与硬件验证、隔离首启改密和主机密钥处理等边界保持不变。
