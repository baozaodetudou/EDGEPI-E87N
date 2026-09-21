# Debian 13 + Linux 6.18.51 LTS 升级

> 本文保留 2026-09-12 的版本升级和旧候选证据。下面的版本、补丁数量、哈希与“屏幕未支持”只描述当时产物。当前版本来自 [e87n-build.json](../userpatches/config/e87n-build.json)，默认镜像已改为预装显示包并启用 LCD/背光，见[当前默认配置](DEFAULTS.md)及[显示/风扇说明](display-fan.md)。[candidate3 验收](FINAL-VALIDATION-20260920.md)也属于此前的冻结配置，不能替代当前显示配置验收。

## 目标与原因

用户要求最终是 Debian/Armbian，并明确要求升级至 Debian 13、使用最新 LTS 内核。原先选择 Debian 12 是首次移植沿用的构建默认值，不是已经确认的 E87N 硬件限制。先前把“最新”误解成最新非 LTS stable 的选择已纠正。

2026-09-12 核对的官方状态：

- [Debian releases](https://www.debian.org/releases/)：稳定版 Debian 13，代号 Trixie；当日最新点版本 13.6。Debian 12 已是 oldstable。
- [kernel.org](https://www.kernel.org/) / [机器可读版本信息](https://www.kernel.org/releases.json)：最新 stable 7.2.5；最新 LTS 6.18.51；6.12 LTS 分支最新 6.12.109。
- **本项目选定并固定 6.18.51 LTS**。7.2.5 只做过源码探索和只读审查，未编译，不用于本次镜像；也不采用 RC 或 linux-next。以上是选型当日记录，不是自动跟随最新版本的配置。

## 当前固定输入

| 输入 | 固定值 |
| --- | --- |
| 目标发行版 | `RELEASE=trixie`，Debian 13 ARM64 |
| Armbian 框架 | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da` |
| 内核来源 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` |
| 内核 pin | `commit:f6388029ea9e2c9e807d73827658738ea131faee`，Linux 6.18.51 |
| 补丁与配置 | `edgepi-e87n-6.18`，12 个补丁；`linux-edgepi-e87n-lts` |

发行版固定为 Trixie，发行版内仍按签名软件源安装更新；Debian 包没有全部锁定到单个历史快照。构建主机或容器的 Debian 版本也不能作为目标镜像发行版证据。

## 已完成的构建与静态验收

- `build-armbian.sh` 默认 `RELEASE=trixie`。显式 `RELEASE=bookworm` 只切换 rootfs 发行版，不会恢复旧 6.12.108 内核输入，也不会把旧镜像重命名为新版。
- `customize-image.sh` 检查目标 rootfs 的 ID、VERSION_ID、VERSION_CODENAME 与请求版本一致；只在 Armbian 的目标 chroot 中刷新所选 Debian 发行版的签名软件源、安装可用更新。不改变 Mac 或 Lima 主机的发行版。
- Trixie 已在固定 Armbian 框架的 `config/distributions/trixie` 中列为支持 ARM64 的 Debian 13 发行版。
- Trixie ARM64 基础 rootfs 已实际获取：`rootfs-arm64-trixie-minimal_202609-665d3a23b595-H6eccde-Bf1b6db.tar.zst`，111,976,556 字节；`e87n-trixie-rootfs-arm64.service` 正常退出。这只是基础系统缓存，不是可启动 E87N 整盘镜像。
- family 已切换到官方 6.18.51 commit，12 个板级补丁已集成，并在真实基线上全部通过 `--fuzz=0` 应用。DTB 编译通过，保留 7 条旧节点结构 warning，不能称为完整 schema 校验通过。
- 定点 ARM64 编译已通过 MAC、PCS、两个 PHY、PWM、LVTS、PCIe 和 CPUFreq-dt 黑名单对象；5 个 clock 与 pinctrl 对象此前也已通过。CPUFreq-dt 黑名单对象通过仅验证禁用路径可编译，不表示 DVFS 已启用。
- 首次完整构建尝试因 Mac AppleDouble 元数据文件失败；清洁传输后重试已通过 Armbian 实际 12 补丁解析与应用。21:40:35 CST 的编译保留缓存后，纳入 PHY 初始化资源/固件预检修复和 `BSPFREEZE=yes` 的最终输入于 22:23:45 继续构建；`Image` 于 23:16:03 生成，全部模块编译完成并于 23:20 进入打包，**2026-09-12 23:25:03 CST 完整构建成功结束**。
- 最终 unit invocation 为 `282bd69e0665409cb6137de1d30b8046`：`active (exited)`、`Result=success`、`ExecMainCode=1`、`ExecMainStatus=0`；这里 `ExecMainCode=1` 表示正常退出类别，不是退出码 1。
- 本轮最终 `.config` 已检查：thermal、PWM fan、efuse、USB-root、MMC 所需配置均为 `y`；`CPU_FREQ=n`、`CPU_THERMAL=n`、`MEDIATEK_2P5GE_PHY=m`、`MTK_NET_PHYLIB=y`。配置满足要求不等于驱动运行或实机启动通过。
- 7.2.5 先前仅完成源码下载和只读支持审查，没有编译该版本或刷写设备。其独立源码缓存保留，但停止推进该版本的移植，不把 7.2.5 的审查结论直接当作 6.18.51 的结论。
- Debian 13 验证器支持 `--release trixie|bookworm`，默认 Trixie。最终主机回归的 132 项 artifact 合成用例与 64 项 image mock 用例通过；真实新版镜像审计是另行执行的检查，不能用合成测试替代。
- 已完成的旧候选仍是 [Debian 12 / Linux 6.12.108](candidate-20260912.md)，按原 SHA256 保留，不覆盖、不改名。
- **真实新镜像已生成且静态审计通过。** 目标系统实际为 Debian 13.6 Trixie，内核为 `6.18.51-current-filogic`；整盘 `verify-image --release trixie --require-usb-root` 已通过 GPT、ext4 `fsck -fn`、UUID、firmware、initrd USB-root 检查，实际 `.deb` 及最终安装 DTB/config 验证也通过。
- 最终镜像额外只读核实 linux-image、DTB、BSP、base-files 均为 hold，且 `Package`、`Version`、`Armbian-Original-Hash` 与唯一对应的本次 `.deb` 一致；没有 `linux-u-boot` 包或意外用户 SSH key，shadow 未发现空口令字段。这不意味强密码，以上仍不是全驱动、实机或安全生产验收。

实际 raw 镜像为 `Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img`，大小 `1149239296` 字节，SHA-256 为 `1697307756412aef6fa4cabd33bb4c115daa78f80d81e43233a689a3e962f6bc`。

**xz 压缩、Mac 完整导出和本地镜像校验均已完成。** `.img.xz` 大小为 `176462804` 字节（约 168.3 MiB），SHA-256 为 `32474999d280d4a9057985c6ba6985b1ed5f223584b7f8fe1809f9e4f48cb33e`；VM 与 Mac 的 `xz --test` 均通过，Mac 压缩文件 SHA-256 和流式解压得到的 raw SHA-256 均与 VM 及上述记录一致。实际主机目录为 `output/releases/2026-09-12-e87n-trixie-lts-6.18.51/`，文件入口、组件校验值和交付记录见 [新候选详情](candidate-trixie-6.18.51-20260912.md)；未覆盖旧 Bookworm 产物。

release 内 `SHA256SUMS` 已生成并全部通过 `shasum -a 256 -c`；host source-build-inputs 的 52 项全部与 VM manifest 匹配。本机 `xz --robot --list` 确认压缩/解压字节数为 `176462804` / `1149239296`，CRC64 正常。本次静态候选的压缩、传输及本地完整性验收已收尾。

## 隔离首启与安全边界

实际镜像保留默认 `root/1234`、串口 autologin 和允许 SSH root 登录的配置；必须隔离首启并立即改密。shadow 未发现空口令字段、未发现意外用户 SSH key，不意味强密码或镜像已安全加固。镜像带有缓存生成的共享初始 SSH host keys，首启再生服务排在 `ssh.service` 之后（`After=ssh.service`），不能假定首次 SSH 连接前已获得唯一主机密钥；确认再生和指纹后再开放网络。root 自动扩容只针对当前根盘，不会因此完成其他盘的迁移或 eMMC 安装安全验证。

## 新 LTS 首版硬件策略

- **CPU DVFS / CPU cooling 禁用。** 移除 CPU OPP 与 CPU cooling-map 引用，保持固件设置的启动频率，不承诺 Linux 调频/调压。旧 DTS 的全部频率共用 850 mV 且缺少 `proc-supply`，没有可据以启用安全 DVFS 的板级供电证据；CPUFreq 补丁阻止旧 OPP 表意外触发 generic 驱动。本轮最终配置已确认 `CPU_FREQ=n`、`CPU_THERMAL=n`，仍不能将其作为实机频率或散热表现的证据。
- **WED 不支持。** MT7987 不注册 WED，普通 MAC/PHY/PCS 支持和硬件卸载是不同能力；不得宣称 WED 加速已可用。
- **LVTS 使用软件轮询。** 不填写猜测的 IRQ；普通和 passive 轮询周期均为 1000 ms。保留真实 efuse 校准来源，校准缺失/截断/非法时拒绝初始化，不能以传感器 probe 失败作为“已有温度保护”。
- **风扇使用四级 `<0 128 192 255>`。** 50/65/75℃三个 active trip 分别绑定 fan 状态 1/2/3，保留 critical trip。删除所有继承的 CPU cooling 引用，避免无 provider 使整个 thermal zone 注册失败。不能沿用 26 级列表配旧 1/2/3 状态，否则 115℃也可能只有 30/255 输出；Debian 系统不依赖旧 OpenWrt 用户态风扇守护程序。
- **NV3007 显示未支持，背光未保证。** vendor 的 `999990-fbtft` 小屏补丁尚待移植，既未纳入本次 6.18.51，也未纳入旧 6.12.108；显示节点存在和 PWM 对象编译通过均不能作为显示或背光正常的证据。
- **原固定 MAC 尚未恢复。** factory MAC 引用已移除，当前允许临时随机 MAC，地址持久化及原 DHCP 绑定不能视为可用。
- **USB 扩展与 RAM fixup 未完成实机确认。** 6.18 驱动忽略 vendor 的 `auto_load_valid` 属性，保留 DTS 属性不代表 vendor 自动校准流程已移植。U-Boot 对 RAM 的 fixup 未验证，实际内存大小、传给内核的内存布局及最终识别容量需从启动日志核实。
- 温度阈值是首版软件控制策略，不是芯片极限参数或硬件关断证明。温控与 PWM fan 链路应内建，实际温度准确性、起转/转速、温升、critical trip 处置和重启/恢复行为仍需上板验证；CPU 降频散热不在当前能力中。

## 后续版本维护标准

每次制作新版先查官方最新 **longterm/LTS** 系列及其维护版本，再固定确切源码版本/提交以便复现。当前选定 6.18.51，后续跟进 LTS 安全与修复更新。出现新版本时，需要重新检查 E87N 设备树、驱动补丁接口和最终配置，重新编译并检查产物；不能自动换成非 LTS stable，也不能直接滚动跟踪一个会变动的分支、跳过测试。

普通 Debian `apt upgrade` 可以更新大部分发行版软件包，但不能自动补齐我们维护的 E87N 内核移植。启动器默认增加 `BSPFREEZE=yes`：Armbian 会锁定它安装的板级/内核产物，防止官方同名 Filogic 包替换 E87N 专用内核和 DTB。锁定也包含 Armbian 重打包的 `base-files`，因此不是“所有 Debian 包均不受影响”。本次最终镜像的 hold 和原始构建 hash 已核验通过；后续每个版本仍需重新核验，不能只看启动参数。首轮编译已保留缓存并被有意替代，最终候选包含该参数和 PHY 初始化资源/固件预检修复，不交付旧输入的中间产物。

内核升级需要更新本仓库并重新生成匹配的内核、DTB、模块和 initramfs；锁定不是停止维护 LTS 安全更新，而是阻止未经 E87N 移植验证的包替换。当前没有创建后台定时任务，也没有自动升级连接设备。

本次已达到静态候选验收和本地镜像交付：真实新镜像生成，实际系统为 Debian 13.6 Trixie，内核及模块匹配 `6.18.51-current-filogic`，GPT/ext4/DTB/config/固件/initramfs 和安装包 hold/hash 检查通过；xz 压缩、Mac 完整导出、xz 完整性及压缩/解压 SHA-256 校验均完成。网口、存储、USB、NVMe、温控、重启和原厂 U-Boot 启动能力仍必须实机测试，默认登录与共享初始 host keys 也必须在隔离首启中处理。不能据此宣称全部驱动正常、实机可用或安全生产就绪；升级到新内核不会解除整盘刷写 eMMC 的风险。
