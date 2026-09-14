# 构建 E87N Armbian

当前配方采用 [DEFAULTS.md](DEFAULTS.md) 定义的 headless 最小系统：`root` / `doumao`、SSH 22 密码登录、networkd/netplan DHCP、`Asia/Shanghai`、`zh_CN.UTF-8` 和正常 APT。显示包单独构建发布，暂不在基础镜像中自动加载。没有首次创建用户向导或强制公钥门槛，额外存储模块默认 `E87N_EXTRA_STORAGE=no`。

当前交付为[原厂 U-Boot 未压缩 USTAR 固件](UBOOT-FIRMWARE.md)，Armbian `.img` / `.img.xz` 只作中间产物或历史证据，不可刷写。R4 本地已生成并独立审计 EXIT 0；R4 重新打包历史 Actions 34737922588 的原始 RAW，修正 DTB 的 1 GiB/保留区及 bootargs（含 902 等效修正），没有完整重编 Armbian 或内核。V3 因内核地址修正已废弃，主机导出及 SHA-256 比对已完成。没有 E87N 重启、刷写、完整恢复备份、已实测控制通道或板上 RAM 测试记录。

[Actions 34737922588](ci-keygen-fix-20260913.md) 与[2026-09-13 屏幕/风扇候选](candidate-display-fan-20260913.md)仅证明各自旧 GPT 配置的构建和审计结果。2026-09-13 11:10:49 CST 的旧配置 VM 构建同样不能作为新固件完成证据。既有历史哈希不变；新格式检查见 [UBOOT-FIRMWARE.md](UBOOT-FIRMWARE.md)。

下列命令均从构建主机的仓库根目录执行。产物路径属于本地构建目录；新 [GitHub Actions](GITHUB-ACTIONS.md) 流程只有成功运行并上传后才有对应 TAR artifacts，远端 Release 发布未确认。

## 固定输入与可复现范围

| 输入 | 当前值与来源 |
| --- | --- |
| Armbian 框架 | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，由 `build-armbian.sh` 固定 |
| 框架兼容修补 | `patches/armbian-build/0001-python-env-path.patch`；修复最小环境中的 Python PATH 引号 |
| Linux stable | `f6388029ea9e2c9e807d73827658738ea131faee`，对应 6.18.51 |
| 内核来源 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git`，使用明确的 `commit:` |
| 板卡 / 分支 | `BOARD=edgepi-e87n` / `BRANCH=current`；family 拒绝其他分支 |
| 目标发行版 | `RELEASE=trixie`；历史屏幕/风扇候选实际为 Debian 13.6，新构建以实际包版本为准 |
| 内核配置 | `userpatches/config/kernel/linux-edgepi-e87n-lts.config`，另有板卡 hook 调整最终配置 |
| 当前补丁目录 | `userpatches/kernel/edgepi-e87n-6.18/`，共 15 个补丁，包括 NV3007、901 GMAC aliases 和 `902-e87n-memory-1g.patch` 的 1 GiB/固件保留区修正 |
| 固件转换 | `scripts/build-factory-firmware.py`、`scripts/prepare-factory-rootfs.py`、`board-support/factory-boot/`；最终为 FIT kernel + 含 `/boot` 的 ext4 root + CONTROL 的未压缩 USTAR |
| 用户空间与固件 | `board-support/`、`packaging/e87n-display/`、`scripts/build-display-deb.sh`、`userpatches/customize-image.sh`、`firmware/`；PHY 固件安装前检查 SHA-256 与大小 |
| 额外存储模块 | `E87N_EXTRA_STORAGE=no`；只有显式设为 `yes` 才请求额外 DM/RAID 等模块 |

这些 pin 固定框架和内核源码，不构成逐字节可复现的整个系统快照。`customize-image.sh` 从 Debian 签名软件源更新软件包并安装 SSH/网络/时间与 locale 基础依赖；`e87n-display` 由独立 job 构建。显示包依赖 `python3`、`python3-pil` 和 `fonts-dejavu-core`，独立版本与升级方法见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)。

重建特定候选时，应保留该候选的仓库输入、框架兼容修补、最终内核配置、构建参数、主机/容器版本、包版本、日志和校验清单。候选记录中的冻结输入归档和 release 整理属于当次人工交付步骤，`build.sh` 不会自动生成同样的 release 目录或证明新镜像与旧镜像哈希相同。

内核不会自动跟踪最新 LTS。升级需要同步检查 family、补丁、配置和验证器；显示/风扇验证器目前明确检查 `6.18.51-current-filogic`。保留的 `edgepi-e87n-6.12/` 是历史补丁集。单独传入 `RELEASE=bookworm` 只改变 rootfs，不会恢复历史内核或重建旧候选。

## Linux 默认入口

在专用 Linux 构建主机的原生、区分大小写的文件系统中构建。主机需要 Bash 5、Git、CA 证书、curl、sudo、xz、常规 Linux 工具，以及安装依赖和创建镜像所需的权限。首次构建需要访问框架、内核和软件包源；已有部分缓存不等于离线构建已受支持。

Debian 13 构建主机的基础依赖可这样准备：

```sh
sudo apt-get update
sudo apt-get install -y bash ca-certificates git sudo curl xz-utils procps util-linux
```

这不是完整编译工具链清单。固定版本的 Armbian 框架负责安装剩余主机工具和编译依赖；板卡 hook 另外请求主机 `initramfs-tools-core`。真实镜像审计的完整工具要求另见 [TESTING.md](TESTING.md)。

```sh
./build.sh
```

`build.sh` 直接转交 `build-armbian.sh`。Linux 分支最后调用 `source/armbian-build/compile.sh`，不会通过本仓库启动器自动创建 Docker 容器或 Lima VM。普通用户需具备框架所需的 sudo 权限；在已经以 root 身份运行的专用构建环境中，使用：

```sh
ALLOW_ROOT=yes ./build.sh
```

启动器传给 Armbian 的默认参数为：

```text
BOARD=edgepi-e87n BRANCH=current RELEASE=trixie
BUILD_DESKTOP=no BUILD_MINIMAL=yes BSPFREEZE=yes
TZDATA=Asia/Shanghai DEST_LANG=zh_CN.UTF-8
KERNEL_BTF=no KERNEL_CONFIGURE=no SHOW_LOG=yes USE_TMPFS=no
KERNEL_GIT=shallow EXTRAWIFI=no CPUTHREADS=4
```

额外参数按原样追加给 Armbian，例如 `./build.sh CPUTHREADS=2`。更改参数后的产物需要重新验证。`BSPFREEZE=yes` 请求冻结 Armbian 内核、DTB、板级包及重打包的 base-files，防止官方同名 Filogic 包替换板级移植，也防止 `/boot`/模块变化而 p4 FIT 未同步。允许 `apt update` / 安装用户空间软件，不要解除内核 hold；没有自动 FIT 更新器，后续内核、DTB、initrd、模块和 FIT/root 必须成套重建审计。

板卡 hook 默认使用 `E87N_EXTRA_STORAGE=no`；需要额外存储模块时可显式运行 `./build.sh E87N_EXTRA_STORAGE=yes`。该选项不预装 RAID/LVM 管理套件，也不改变 ext4 根分区或部署数据盘。默认最小系统仍保留常规 Linux 与板级驱动，详细配置和验收范围见 [OPTIONAL-STORAGE.md](OPTIONAL-STORAGE.md)。

## 框架准备与重复构建

首次启动会在 `source/armbian-build/` 初始化仓库并浅层获取固定提交。已有 checkout 与期望提交不符时会停止并保留现场；不会自动切换已有源码。`scripts/prepare-framework.sh` 只接受上表中的框架提交，检查后幂等应用 PATH 修补，遇到冲突直接失败。框架 HEAD 保持固定提交，工作树包含这项已知修补。

虽然启动器读取 `ARMBIAN_REF` 环境变量，准备脚本仍强制检查上述提交，因此它不是任意升级框架的开关。不同框架版本需要先移植和验证兼容修补。

准备完成后，启动器把原框架 `userpatches/` 移入新建的 `userpatches-backup.*` 目录，再复制本仓库的 overlay，加入 `firmware/`、`board-support/`、选定的 `docs/` 文档，以及显示包构建脚本和 `packaging/`。系统说明安装到 `/usr/share/doc/e87n/`，显示包自身说明安装到 `/usr/share/doc/e87n-display/`。手动同步 VM 输入时也要复制 `docs/` 和 `packaging/`。后续修改应落在本仓库的输入文件中；直接修改框架内复制的 overlay 不会自动回传。

同一源码/缓存目录只运行一个构建，不在构建中途更换输入。macOS 有运行容器检查和容器内锁，Lima helper 使用 VM 工作区锁；普通 Linux 入口没有统一的外层锁来阻止所有其他手动构建进程。

## macOS 默认使用 Docker

主机需要 Bash、Git 和可用的 Docker CLI/运行环境，且 Docker 能运行 privileged Linux 容器。默认入口仍为：

```sh
./build.sh
```

默认容器镜像为 `ghcr.io/armbian/docker-armbian-build:armbian-debian-trixie-latest`。也可明确选择 Debian 基础镜像，由入口脚本和 Armbian 安装依赖：

```sh
E87N_BUILD_IMAGE=debian:13.6-slim ./build.sh
```

`E87N_BUILD_IMAGE` 选择构建主机镜像，不锁定目标 rootfs 包版本。严格记录环境时应保留实际容器镜像 digest，而不只记录标签。`E87N_BUILD_CPUS`、`E87N_BUILD_MEMORY` 默认分别为 `4`、`4g`；调整 Docker 资源与编译线程数是不同的设置。

启动器会在替换 overlay 前检查名称匹配 `e87n-armbian-` 的运行容器，发现已有构建时返回 75。新容器以 detached 方式运行，名称和日志路径会打印出来；启动器跟随日志，并用 `docker wait` 的真实退出码返回结果。客户端断开后构建可能继续，重试前先核对打印出的容器状态与日志。

容器通过两个命名卷保存 `cache/` 和 `.tmp/`：`e87n-armbian-cache`、`e87n-armbian-tmp`。这让内核源码、符号链接、设备节点和镜像挂载使用 Linux 文件系统。容器及缓存会保留用于诊断，不会在结束时自动删除。主机构建日志位于 `source/armbian-build/output/logs/launcher-*.log`。

## Lima：手动准备输入，helper 跟踪固定单元

Lima 是可选构建环境，不是 `build.sh` 的 macOS 默认后端。`scripts/lima-e87n.yaml` 提供已固定镜像 SHA-512 的 Debian 13 ARM64 主机配置：Lima 至少 2.0.0、VZ、4 核、8 GiB 内存、64 GiB 稀疏磁盘。此配置使用 macOS 的 ARM64 虚拟化环境，不是通用 x86 VM 模板，也不是目标板卡镜像。

使用 helper 前需自行完成：

1. 安装 Lima，并创建、启动可用的构建 VM。配置文件不包含自动安装构建依赖的 provision 步骤。
2. 在 VM 中准备本仓库及 Linux 基础依赖，确保 `sudo -n` 可用，且具备 systemd、`flock`、`pgrep`、GNU `find` 和 tar。
3. 将本次仓库输入手动复制到 VM 原生文件系统的工作目录，默认 `/srv/e87n`。至少保留构建脚本、`scripts/`、`patches/`、`userpatches/`、`firmware/`、`board-support/`、`packaging/` 和 `docs/` 的相对关系；若要运行测试，同时复制 `tests/` 和 `README.md`。
4. 核对传入文件、权限和符号链接；macOS 归档需排除 AppleDouble `._*` 与 `.DS_Store` 元数据。配置不共享 Mac 目录，因此主机改动不会自动同步。旧源码、输出和缓存的取舍也需要自行确认。

框架可由 VM 内首次 `build.sh` 获取；如需提前准备缓存，则必须先单独准备好固定框架 checkout。`build-lima.sh` 不负责 VM 创建/启动、依赖安装、输入同步或自动缓存预热。

helper 的默认配置为 `E87N_LIMA_VM=e87n-armbian`、`E87N_LIMA_DIR=/srv/e87n`。这两个变量只选择 VM 和工作目录，**服务名始终是 `e87n-armbian-build.service`**：

```sh
./build-lima.sh status
./build-lima.sh logs
# 仅当传统单元不存在，且没有其他构建/锁持有者时提交：
./build-lima.sh build
```

`build` 提交固定名称的 systemd 服务，并设置 `ALLOW_ROOT=yes` 和非交互环境。提交成功只表示已接受任务。它不会停止、重置或替换已有单元，即使旧单元已退出；存在旧单元时应先检查其记录并人工处理后续构建安排。

| `status` 结果 | 含义 |
| --- | --- |
| 0 | `active/exited`、正常进程退出 0、`Result=success`，且有晚于该次启动时间的非空 `Armbian*.img` 或受支持压缩镜像 |
| 75 | 单元仍在运行；构建/导出遇锁冲突时也使用 75 |
| 3 | 不能证明正常成功完成，例如单元不存在、状态或启动时间不可用 |
| 4 | 进程成功，但没有符合条件的新非空镜像 |
| 构建的非零退出码 | 可确认的正常进程失败会原样传回；需结合日志区分同值状态码 |

`Result=success` 与 `active/running` 同时出现不代表完成。即使 `status` 返回 0，也只证明进程结果和中间镜像文件存在性，不证明最终 TAR 转换、厂商解析器或固件审计通过。

传统单元确认完成后，可导出输出快照：

```sh
./build-lima.sh export
```

默认在主机 `output/lima/` 下创建新目录，保存 `output.tar`；`E87N_LIMA_EXPORT_DIR` 可覆盖该父目录。也可传入一个尚不存在、父目录已存在的目标目录。导出前仍核对单元、启动时间和新镜像，并获取工作区锁；VM 快照会保留。快照包含整个 Armbian `output/`，可能含历史日志或其他旧文件，不能将其中每个文件都视为本轮产物。helper 不自动解包、审计或发布快照。

### 历史屏幕/风扇候选使用另一个单元

2026-09-13 屏幕/风扇候选实际使用 `e87n-display-fan-build.service`，09:05:25 CST 开始、09:15:45 CST 成功退出。这个服务是当次人工安排的独立构建单元。上面的 helper 无法选择它；旧单元的 `status`、`logs` 或 `export` 不能作为本次构建或导出的证据。

在保留该单元的原构建 VM 上，以下命令仅用于查看记录：

```sh
limactl shell --tty=false --workdir=/ e87n-armbian sudo -n systemctl show \
  e87n-display-fan-build.service \
  --property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp
limactl shell --tty=false --workdir=/ e87n-armbian sudo -n journalctl --all \
  --unit=e87n-display-fan-build.service --no-pager --lines=200
```

新 clone 或新 VM 不会包含该历史单元及其记录。该候选的导出、压缩和校验是另行完成的，详见[候选记录](candidate-display-fan-20260913.md)。

## 可选内核缓存与产物位置

`scripts/seed-kernel-cache.sh` 仅在 Linux 主机上、固定框架 checkout 已准备好且没有运行构建时使用：

```sh
bash scripts/seed-kernel-cache.sh current
```

它读取当前 family 的内核 pin，获取并验证浅层 Git 对象，准备 `source/armbian-build/cache/git-bare/shallow-kernel-6.18`。它不 checkout、打补丁或编译，不会替换已有缓存；发现未完成缓存会保留并退出。它同时获取工作区 `.build.lock` 和缓存 `e87n-build.lock`，但不能阻止不遵守这些锁的手动进程。

完整构建的原始输出相对于仓库根目录为：

```text
source/armbian-build/output/images/   GPT 中间镜像（不可刷写）
source/armbian-build/output/debs/     内核、DTB 等 Debian 包
source/armbian-build/output/logs/     构建日志
output/ci/firmware/                  CI 最终 -uboot-firmware.tar
```

镜像与压缩副本可能沿用相同文件名。记录本轮输入、时间和校验值，按同一次构建选择内核/DTB 包；不要仅凭旧文件仍存在判定成功。构建结束后按 [TESTING.md](TESTING.md) 检查实际产物。

`BOOTCONFIG=none` 仅跳过启动链构建/注入。完整镜像仍包含新 GPT，不能刷写；历史 extlinux/独立 bootfs 路径仅适用于中间或旧产物，新 TAR 从原 p4 的 FIT 启动。

## 从 GPT 中间镜像生成 U-Boot TAR

在专用 Linux 构建主机完成实际 raw 镜像审计后，使用普通 `.img` 文件作为输入：

```sh
sudo -n python3 scripts/build-factory-firmware.py \
  --image /path/to/Armbian-candidate.img \
  --output /path/to/candidate-uboot-firmware.tar
sudo -n python3 scripts/verify-factory-firmware.py /path/to/candidate-uboot-firmware.tar
```

转换器处理主机私有文件副本，不连接板卡或修改输入镜像。需要 Linux root、loop/只读挂载工具、Python 3、e2fsprogs、device-tree-compiler、u-boot-tools、initramfs-tools-core 及主机 C 编译器等；完整依赖以脚本和 CI 准备步骤为准。`./build.sh` 生成中间镜像不等于已完成这两步。

新实现把原 bootfs 复制到 root 内 `/boot`，移除旧独立 `/boot` 挂载，禁用通用 Armbian resize 并安装严格验证布局的 p5 专用 resize2fs 服务；factory MAC helper 在 DHCP 前只读 p2 的 `0x24`/`0x2a`。内核/DTB hold 必须保留。

本轮转换改为**重新整理 ext4**：创建相同 UUID 的全新 **735 MiB ext4**，用 `cp -a --preserve=all` 复制完整目录树，不裁剪必要组件；文件 SHA、属主、模式、硬链接及 xattrs 比较已通过。首次最小化旧文件系统仍约 820 MB，不适合上传上限。R4 已完成独立最终审计，首次 ENOSPC 与磁盘临时目录复验过程见[固件记录](UBOOT-FIRMWARE.md)。

外层为未压缩 USTAR，仅有 `sysupgrade-edgepi-e87n/{kernel,root,CONTROL}`。FIT 使用 LZMA 内核、原始 initrd 和 DTB；root 为原始 ext4。最终完整 TAR 必须 `<=768 MiB`，FIT 必须容纳于 32 MiB p4，root 及厂商尾部 512 KiB 擦除须在 p5 内。此大小限制不是 Web 空闲 RAM 实测结果。

本轮原生 VM 已报告：902 `--dry-run --fuzz=0`、加强后的 factory 24 项、root adapter 24 项、完整 Linux `ci-regressions.sh` 全套及静态 CI 85 项通过；新增 runtime/root preparer 两个编译检查目标也已复验通过。首次转换的 LZMA kernel 5993493 字节、原始 initrd 16328217 字节、DTB 21479 字节，FIT 可容纳于 32 MiB p4。V3 已废弃；R4 TAR 793057280 字节、root 770703360 字节，独立最终审计 EXIT 0，准确文件名和 SHA-256 见[下载说明](DOWNLOADS.md)。

实际 ARM64 Image 头的 `text_offset=0`、有效 `image_size=0x1690000`（23658496 字节）、`flags=0xa` 决定 R4 FIT 的 kernel load/entry 改为 **`0x40000000`**（2 MiB 对齐 RAM 基址），配合 1 GiB DTB。新审计显式核对对齐与 image_size 范围，拒绝非法 Image 头、FIT loadables、非空 FIT reservation map 和额外 init 参数，要求 root 使用 4 KiB ext4 块；不能沿用 V3 审计结论。依据见 [Linux ARM64 booting](https://docs.kernel.org/arch/arm64/booting.html)，旧地址/重定位差异见[固件契约](UBOOT-FIRMWARE.md)。

本次 R4 最终验证已对同一 TAR 独立重跑全部检查并 EXIT 0，随后以不覆盖已有文件的方式暴露产物；主机导出及 SHA-256 比对已完成。源 RAW SHA-256 为 `289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9`，不是本轮完整新内核编译。完整 Linux regressions 的 `regressions-disk.log` EXIT 0，此前缺 docs/AppleDouble 与 tmpfs 满失败均已解决并保留日志；新增两个编译检查目标也已复验通过。未来新源码工作流未 dispatch，远端发布未确认。刷写前仍需完成板上 RAM 测试、独立备份，并确认串口或已经实测可恢复的 U-Boot Web/网络控制通道及物理恢复路径，当前均未实测。

正常 TAR/FIT 的 initrd 会按原 `.img` root UUID 挂载磁盘根文件系统，不是独立 RAM 诊断系统。刷写前诊断需另备仅驻留 RAM 或隔离的测试 rootfs，并确认不会挂载/扩容原 eMMC，详见[首启准备](first-boot.md)。满足用户“完全准备好再刷”的条件前不刷写，本文不提供设备写入步骤。
