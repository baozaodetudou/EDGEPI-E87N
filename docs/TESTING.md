# 测试与验证 E87N Armbian

当前测试目标是 [DEFAULTS.md](DEFAULTS.md) 中的最小配置；本轮云端完整构建和实际镜像静态审计已通过，另有可丢弃 rootfs 副本的用户空间集成结果，详见[本轮记录](ci-keygen-fix-20260913.md)。诊断、网络与系统静态检查分别使用 `tests/test-doctor.py`、`tests/test-network-policy.py` 和 `tests/test-verify-system.py`；最后一项需要专用 Linux 构建环境的 root 身份来创建临时 root-owned 夹具，不挂载设备。网络测试要求 GNU patch，macOS 可用 `gpatch`，并会打印实际工具版本。

基础 headless 镜像必须执行 `verify-image.sh --release trixie --require-usb-root --headless --require-system candidate.img`。`--require-system` 核对 `root` / `doumao` 密码登录配置、首次 SSH 前生成独立身份的服务依赖、无串口自动登录、无旧初始化服务、networkd/netplan DHCP、上海时区、中文 UTF-8、签名 APT 源和真实 DTB 的 GMAC aliases；显示包单独执行显示包和静态显示审计。这些都是静态检查，不执行镜像程序，也不能证明 SSH 已能登录。

历史上，2026-09-13 的 **Debian 13.6 Trixie / Linux 6.18.51 屏幕/风扇候选**已完成完整构建、真实镜像只读审计和导出校验，尚未上板启动或测试。已有结果与精确哈希见[历史候选记录](candidate-display-fan-20260913.md)。2026-09-13 11:10:49 CST 完成的旧配置 VM 构建也已被当前最小配置取代，不能作为当前测试结果。本文提供复跑入口，不把命令示例当作新的通过记录。

所有仓库命令从根目录执行。测试主机与 E87N 目标板应明确区分：名称中的 `test-hardware.py` 是假硬件夹具测试，不是上板测试程序。

## 各类检查能证明什么

| 检查 | 输入与范围 | 不能据此认定 |
| --- | --- | --- |
| Python / shell fixture 回归 | 临时文件、假 sysfs、模拟设备及进程状态 | 真实内核、镜像或板卡通过 |
| 框架加载 / 补丁发现与应用 | 固定框架、14 个补丁、指定 Linux 基线；可选实际 DTB 编译 | 完整内核已编译或设备可启动 |
| 真实包 / 配置 / DTB 静态检查 | 本轮实际生成的文件 | rootfs、initramfs 或所有硬件正常 |
| `verify-image.sh` | 完成的 raw 镜像、真实只读 loop/GPT/ext4/UUID/initramfs；显式启用 USB-root、显示/风扇与最小系统检查 | U-Boot 能加载、首启成功或 eMMC 写入安全 |
| `smoke-minimal-userspace.sh` | 可信候选的可丢弃 rootfs 副本执行最新 customize、APT、locale 与隔离 loopback SSH/PAM | 新内核/镜像构建、完整 PID 1 启动、实体网口或板卡通过 |
| 上板证据采集 | 已经启动的 Linux 板卡日志和只读状态 | 屏幕效果、风扇起转、负载稳定性或完整硬件验收 |

## 不需要构建源码或设备的 fixture 测试

使用开发主机上的 Python 3.12 或 3.13、Bash，以及 Pillow、DejaVu 字体和设备树工具。Debian 13 主机可安装：

```sh
sudo apt-get update
sudo apt-get install -y bash python3 python3-pil fonts-dejavu-core \
  device-tree-compiler zstd xz-utils
```

安装依赖需要网络；依赖就绪后，下面的 fixture 测试不启动 Docker/VM，不接入板卡，也不获取源码。Python 测试直接注入仓库的 `board-support/`，无需将程序安装到开发主机或启用显示服务。

`test-display.py` 需要 `DejaVuSans.ttf` 和 `DejaVuSans-Bold.ttf`。非 Debian 主机应自行准备 Pillow 和字体，并在需要时设置 `E87N_TEST_FONT_DIR` 指向已有字体目录，例如：

```sh
(
  umask 022
  E87N_TEST_FONT_DIR="$PWD/test-fonts" python3 -B tests/test-display.py
)
```

此示例假定 `test-fonts/` 已包含两种字体；测试不会下载字体。没有显式变量时先使用系统 DejaVu 路径，再尝试本地 `output/runtime/display-fonts/`；后者不是新 clone 自带的依赖。

完整的主机 fixture 组可在子 shell 中逐项运行，任一失败即停止：

```sh
(
  set -eu
  umask 022
  export PYTHONDONTWRITEBYTECODE=1

  python3 -B tests/test-hardware.py
  python3 -B tests/test-display.py
  python3 -B tests/test-verify-display-fan.py
  python3 -B tests/test-doctor.py
  python3 -B tests/test-network-policy.py

  for test in \
    tests/test-launcher.sh \
    tests/test-lima-launcher.sh \
    tests/test-verify-artifacts.sh \
    tests/test-verify-initramfs.sh \
    tests/test-verify-image.sh \
    tests/test-verify-lts-platform.sh \
    tests/test-collect-board-evidence.sh
  do
    bash "$test"
  done
)
```

这是调用现有脚本的 shell 示例，仓库没有另一个统一测试运行器。通常不需要 sudo；Python hardware 测试在临时目录中注入模拟身份，生产硬件接口不会被调用。

系统配置的 root-owned 夹具应在专用 Linux 构建环境中单独运行，需要主机 `libcrypt` 以核对公开默认密码，不读取设备密码：

```sh
sudo python3 -B tests/test-verify-system.py
```

该测试不挂载镜像、不启动 SSH，也不验证真实登录。显示包的构建与维护脚本验证另见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)。

明确使用 `umask 022`，使夹具中普通目录/文件的创建权限符合配置校验预期。继承 `umask 002` 会产生组可写配置目录并触发拒绝；应修正测试环境，不应为了夹具通过而放宽生产校验。历史候选记录中的三组 Python 测试在 Python 3.13.5 下分别通过 42、38、32 项，共 112 项；这个计数属于当时输入，新改动应记录自己的测试输出。

| 入口 | 主要覆盖 |
| --- | --- |
| `tests/test-hardware.py` | 假 sysfs 遥测、反向背光、配置验证/持久化、失败传播及无风扇写入 |
| `tests/test-display.py` | 模拟 framebuffer ABI、像素打包、边界与四页渲染；预览使用固定数据 |
| `tests/test-verify-display-fan.py` | 合成 rootfs/config/DTB、静态验证器的拒绝条件；可选真实主机 `dtc`/`fdtget` 集成 |
| `tests/test-doctor.py` | 假 proc/sys 中的身份、内存、遥测与隐私边界；始终不宣称硬件验收 |
| `tests/test-network-policy.py` | 用 GNU patch 重建 DTS 别名；可选精简 DTB 夹具，不测试真实网口 |
| `tests/test-verify-system.py` | 专用 Linux/root 夹具中的最小配置、服务、软件包与旧初始化残留拒绝条件 |
| `tests/test-launcher.sh` / `tests/test-lima-launcher.sh` | Git/Docker/Lima/systemd 等外部动作均模拟，检查启动协议、状态码和导出条件 |
| `tests/test-verify-artifacts.sh` | 合成 Debian 包、ELF/DTB/rootfs、版本与 UUID/路径拒绝条件 |
| `tests/test-verify-initramfs.sh` | 合成内核配置、initramfs 文件清单和模块依赖图 |
| `tests/test-verify-image.sh` | loop、mount、fsck、initramfs 等均为 mock；不实际挂载，即使在 Linux/root 下运行 |
| `tests/test-verify-lts-platform.sh` | 用主机 `dtc`、`fdtget`、`fdtput` 编译/修改独立合成 DTB |
| `tests/test-collect-board-evidence.sh` | 注入 FakeHost；不在本机或板卡上运行真实采集流程 |

缺少 `dtc`/`fdtget` 时，显示/风扇验证器测试会跳过集成项；LTS 平台 fixture 则要求 `dtc`、`fdtget`、`fdtput` 齐全并会报错。缺少 `zstd` 时，包验证 fixture 会跳过 zstd 场景。报告应保留 `SKIP`，不能将跳过项写成通过。有些 shell 测试保留临时目录以便诊断，路径会打印出来。

## 可丢弃 rootfs 副本的用户空间集成

同日原生 ARM64 Debian 13 VM 回归结果：`tests/test-hardware.py` 53 项、`tests/test-display.py` 43 项、`tests/test-display-package.py` 19 项、`tests/test-ci-workflow.py` 17 项全部通过。这些分别是硬件夹具、显示代码、软件包与工作流测试，不是实体硬件测试或 GitHub Actions 成功 run。

2026-09-13 已在原生 ARM64 Debian 13 VM 完成：16 项 `test-verify-system.py` 夹具通过；最新 customize hook 在旧候选的可丢弃 rootfs 副本中安装版本化 `e87n-display` 包、设置 root 密码 `doumao`，实际副本的 `verify-system.py` 静态检查通过。签名 APT 源更新、安装并执行 `hello` 成功，`locale charmap` 返回 `UTF-8`。独立网络 namespace 的 loopback SSH/PAM 真实 root 密码登录成功；新生成密钥彼此唯一，重复生成保持已有密钥。原始输入镜像测试前后内容未变。

上述为早期记录。本轮修复后，系统夹具已扩至 27 项，Linux 完整回归及云端 `validate` 均通过；以首轮云端最小镜像为输入的可丢弃副本也重新完成同一 SSH/APT/locale/身份集成检查，退出 0。真实 Debian SSH keygen 单元及负例现已纳入回归，见[修复记录](ci-keygen-fix-20260913.md)。

重复此集成检查需要可丢弃的原生 ARM64 Linux 构建 VM、root、loop/mount、mount/PID/network namespace、chroot、SSH 客户端及静态验证器所需工具，并需要访问签名 Debian 软件源和足够空间复制 rootfs。从仓库根目录运行，输入为可信、构建已结束且不再变化的普通 raw 镜像文件：

```sh
sudo bash tests/smoke-minimal-userspace.sh /absolute/path/to/existing-candidate.img
```

脚本只读挂载输入后复制 bootfs/rootfs；所有 customize、软件安装和密钥生成写入专用副本，使用 `policy-rc.d` 抑制安装过程启动服务。SSH 单独在新网络 namespace 的 `127.0.0.1:22222` 测试，镜像默认 SSH 端口仍为 22。脚本比较输入镜像测试前后的哈希，结束时释放自身挂载并保留打印出的副本目录供检查；它不是纯静态验证器。

这不是完整系统启动：测试使用 VM 内核，副本保留输入候选的内核与布局，也可能保留旧软件包。通过结果不证明新内核裁剪、最终最小包集合、新镜像构建、PID 1 服务启动顺序、真实 DHCP/DNS/NTP、显示或风扇。此测试不产生可交付镜像或物理启动结果；云端原始镜像有其独立校验值，不要把该测试副本当作交付物。相关状态同时记录于 [SYSTEM-READINESS.md](SYSTEM-READINESS.md)。

## 需要已准备框架或 Linux 基线的检查

这组检查不在上面的 fixture 循环中。先按 [BUILDING.md](BUILDING.md) 准备固定 Armbian 框架和 PATH 修补，默认位置是 `source/armbian-build/`。这些测试不会替你创建缺失的 checkout。

```sh
# 使用 Bash 5；运行真实框架的 family/architecture 配置加载器。
bash tests/test-board-config.sh
# 在 Linux 构建主机使用 Bash 5、/usr/bin/python3 和 Git。
bash tests/test-python-path.sh
```

两者均可用第一个参数指定已有框架目录。`test-board-config.sh` 检查当前 pin、14 个补丁、启动配置与内核配置 hook；它没有编译内核。`test-python-path.sh` 执行框架中的真实 Python 环境赋值，并实际调用 `git --version`，检查最小环境的 PATH 修补，不是纯 mock 测试。

补丁发现测试使用框架自己的 Python parser：

```sh
uv run --script tests/test-patch-discovery.py
```

需要已安装 `uv` 和 Python >= 3.12；脚本声明的依赖为 `GitPython==3.1.62`、`unidiff==1.0.0`、`Unidecode==1.4.0`、`rich==15.0.0`、`PyYAML==6.0.3`。`uv` 首次准备环境可能下载依赖。已有满足要求的构建 Python 环境也可直接运行 `python3 -B tests/test-patch-discovery.py`。可依次传入框架目录和 userpatches 目录；默认读取本仓库。此项验证 14 个文件的发现、排序及解析，不执行补丁应用或编译。

若已准备包含固定提交及所需对象的 Linux stable Git 仓库，可实际应用补丁：

```sh
bash scripts/check-kernel-patches.sh ./source/linux-stable
# 可选：同一检查再用主机 C 预处理器、dtc、fdtget 编译 E87N DTB。
CHECK_DTB=yes bash scripts/check-kernel-patches.sh ./source/linux-stable
```

`./source/linux-stable` 是手动准备的示例路径，不是启动器保证生成的 checkout。脚本默认从 family 读取 `f6388029ea9e2c9e807d73827658738ea131faee` 和 `edgepi-e87n-6.18`，将受影响基线文件取到独立临时目录后逐个以 `--fuzz=0` 应用；不会修改所给仓库的工作树。部分克隆缺少对象时 Git 可能尝试获取对象，不能把此项无条件称为离线测试。

需要 GNU patch；macOS 的 GNU patch 可通过 `PATCH_BIN=gpatch` 指定。可选 DTB 编译还需要 `cc`（或 `CPP_BIN` 指定的 C 预处理器）、`dtc`、`fdtget`。脚本保留审计目录和日志，遇第一个补丁失败即停止。补丁零 fuzz 应用和 DTB 编译不等于完整内核编译或完整 DT schema 检查；保留并审查 `dtc.log` 的 warning。

## 实际产物的静态检查

以下 `./artifacts/current/` 是用户自行准备的本轮产物目录示例，需替换成实际路径，不由验证脚本自动生成。选择同一次构建的文件，尤其注意不同日期候选可能使用相同镜像/包名。

包和已提取目录检查不要求 mount/root 权限；Python 至少 3.8，zstd 压缩输入另外需要主机 `zstd`：

```sh
bash scripts/verify-artifacts.sh --debs ./artifacts/current/debs
bash scripts/verify-artifacts.sh --release trixie \
  --extracted-rootfs ./artifacts/current/rootfs \
  --boot-dir ./artifacts/current/bootfs
```

`--debs` 递归检查恰好一套匹配的 arm64 `linux-image-current-filogic` 和 `linux-dtb-current-filogic` 包；不安装包，也不执行维护脚本。此模式不检查 Debian rootfs 身份、固件、extlinux 或 initramfs。不要将多个构建的内核/DTB 包混在同一个检查目录。

目录模式核对实际 extlinux 选择的 Image/DTB、最终配置、模块、Debian 身份、PHY 固件及 root UUID 配置关系。它不会自行提取镜像，也不能仅凭配置文本获得真实 ext4 UUID。`--boot-dir` 必须保持原 bootfs 的路径/链接布局；release 中便于审计的扁平 boot 组件集不一定满足这个要求。合成输入应标记 `--fixture`，该标记不会绕过检查。

LTS 平台策略与显示/风扇静态检查可单独对已经提取的真实文件运行，需要主机 `fdtget`：

```sh
bash scripts/verify-lts-platform.sh \
  --config ./artifacts/current/bootfs/config-6.18.51-current-filogic \
  --dtb ./artifacts/current/bootfs/dtb-6.18.51-current-filogic/mediatek/mt7987a-edgepi-e87n.dtb

python3 -B scripts/verify-display-fan.py \
  --rootfs ./artifacts/current/rootfs \
  --config ./artifacts/current/bootfs/config-6.18.51-current-filogic \
  --dtb ./artifacts/current/bootfs/dtb-6.18.51-current-filogic/mediatek/mt7987a-edgepi-e87n.dtb
```

前者检查 Linux 6.18 的 DTB/config 板级策略，包括以太网资源、LVTS/PWM/efuse 和禁用 CPU DVFS；后者检查当前 6.18.51 显示/风扇配置、DTB、模块/依赖文件、Python 语法、默认 JSON 和服务启用关系。不会导入目标程序、运行显示服务或测试实体面板。`verify-image.sh` 的显示/风扇开关会调用后者，**不会自动调用 `verify-lts-platform.sh`**。

额外存储默认 `E87N_EXTRA_STORAGE=no`，最终配置须核对可选 DM/RAID 等驱动未启用。`verify-lts-platform.sh --require-storage` 仅用于显式以 `E87N_EXTRA_STORAGE=yes` 构建的配置；不要对默认最小镜像强制要求额外模块。开关、配置检查和实际模块验收边界见 [OPTIONAL-STORAGE.md](OPTIONAL-STORAGE.md)。

## 完整 raw 镜像只读审计

在 Linux 构建主机上运行，需要 root、loop 和 mount 权限。macOS 不能直接运行实际镜像审计；脚本不会自动启动容器或 VM。Debian 13 审计主机的工具包为：

```sh
sudo apt-get install -y bash coreutils python3 util-linux fdisk mount gdisk \
  e2fsprogs u-boot-tools initramfs-tools-core device-tree-compiler zstd xz-utils
```

其中 `fdisk` 提供 `sfdisk`，`gdisk` 提供 `sgdisk`，`u-boot-tools` 提供 `dumpimage`，`initramfs-tools-core` 提供 `lsinitramfs`；显示/风扇与系统检查需要 `device-tree-compiler` 中的 `fdtget`。`--require-system` 还需要主机 `libcrypt`。仅安装 fixture 依赖不能满足真实镜像审计的全部要求。

输入必须是构建已经结束、来源可信且检查期间不再改动的普通 raw `.img` 文件，不能是符号链接、压缩包、块设备或字符设备。若手上只有 `.img.xz`，先校验并保留压缩原件解压；已有完整 raw 文件时跳过解压步骤：

```sh
(
  set -eu
  xz --test ./artifacts/current/candidate.img.xz
  xz --decompress --keep ./artifacts/current/candidate.img.xz
)
```

不要用强制覆盖来处理已有同名 raw 文件，应先确认所属候选。对当前最小配置使用完整参数：

```sh
sudo bash scripts/verify-image.sh --release trixie \
  --require-usb-root --headless --require-system \
  ./artifacts/current/candidate.img
```

审计历史镜像时，应使用其原记录对应的参数；历史 Debian 12 镜像需明确 `--release bookworm`。省略当前配置要求或检查旧文件，都不能产生新最小配置的通过记录。

实际检查流程包括：

1. 检查 GPT 及两个分区布局：boot 起点 16 MiB、大小 256 MiB，root 起点 272 MiB。
2. 对打开的镜像文件新建专属只读 loop，确认分区只读、ext4 类型，并执行 `e2fsck -fn`，不修复文件系统。
3. 在专用临时目录中以 `ro,noload,nodev,nosuid,noexec` 挂载，读取真实 root UUID 并调用产物检查。
4. 用主机 `dumpimage`/`lsinitramfs` 检查 extlinux 选择的 initramfs，确认 `/init` 存在，不执行它。`--require-usb-root` 另外依据最终配置及 `modules.dep` 核对对应版本的 USB/SCSI/T-PHY 模块和递归依赖。
5. `--headless --require-system` 调用基础系统静态验证器；显示包单独调用显示/风扇静态验证器。结束时只释放本次创建的挂载和 loop，保留审计文件。

检查或清理失败都会返回非零结果。若清理失败，按打印的所属路径人工检查；脚本不会强制卸载、全局清理 loop，或拆除来源不符的挂载。`PASS` 只代表上述静态范围，不证明真实 U-Boot 能读取 USB、识别 extlinux、执行 `booti` 或启动这张镜像。

## 导出校验与记录结果

如果产物目录附带 `SHA256SUMS`，在该目录内校验。例如 Linux 主机：

```sh
(
  set -eu
  cd ./artifacts/current
  sha256sum --check SHA256SUMS
)
```

macOS 对应命令为 `shasum -a 256 -c SHA256SUMS`。同时检查 xz 完整性；如需确认压缩文件对应的 raw 哈希，可在 Bash 中启用管道失败传播后读取解压流：

```bash
(
  set -euo pipefail
  xz --test ./artifacts/current/candidate.img.xz
  xz --decompress --stdout ./artifacts/current/candidate.img.xz | shasum -a 256
)
```

将输出与该候选记录的 raw SHA-256 比对。自行生成的清单只能检测后续内容变化，不能单独证明来源可信。文件传输完成和压缩完整性通过也不代替真实镜像审计。

记录输入版本、构建/测试环境、命令及参数、退出码、跳过项、日志和被测文件哈希。候选记录还包含已安装程序与冻结源码逐字节比较、包 hold/制品标识核对和单独的 systemd 服务静态检查；这些附加证据不是 `verify-image.sh` 一个命令自动完成的全部内容。

## 后续上板验收范围

副本中的 APT、locale、SSH/PAM 和密钥检查已通过上述限定范围；当前配置在实体 E87N 上的启动、SSH 与 host keys 首启时序、DHCP/DNS/NTP、时区与 locale、APT 安装、显示包升级、面板 probe/颜色/方向、亮度与设置持久化、风扇实际起转、温度校准与负载温升仍待验证。网口、eMMC、USB、NVMe、MAC 持久化和重启也不能从 fixture 或镜像静态结果推断为通过。DTS 默认 256 MiB 与原 OpenWrt 记录的实际 1 GiB 之间的 RAM fixup 仍需核对。CPU DVFS 仍禁用，MT7987 WED 仍不支持；没有测速反馈就不能报告 RPM。

先按[首启与写入边界](first-boot.md)准备隔离、可恢复的启动方式。下列采集命令只供后续**已经启动的 Linux 板卡**手动运行，不属于主机 fixture 组，也不负责启动板卡：

```sh
bash scripts/collect-board-evidence.sh
```

采集器需要 Python 3、可用的 Linux 状态工具与日志读取权限；会在新建私有临时目录写报告，可用 `--output` 指定父目录已存在的新目录。它读取有限的系统/DT/PHY/thermal/hwmon/PWM 状态和日志，不提权、不远程连接、不配置接口、不导出 PWM 通道，也不写硬件控制节点。缺失工具、权限或数据会记录为未采集，返回非零；即使完整采集返回 0，报告仍为 `HARDWARE=NOT-VALIDATED`。

屏幕/背光的人工操作与验收项目见[屏幕与风扇说明](display-fan.md)。假数据预览不是屏幕照片；已有 OpenWrt 硬件参考也不构成新 Armbian 的实机证据。完整 GPT 镜像不是原厂 eMMC 安装器，本页不包含任何 eMMC 写入命令。
