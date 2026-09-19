# GitHub Actions 手动构建与 tag 发布

唯一工作流是 [build-e87n.yml](../.github/workflows/build-e87n.yml)，名称为 **E87N Debian 13 release**。**`on.workflow_dispatch` 不声明任何 inputs，只能手动运行；push、tag push、PR 和定时任务均不触发。** 每次运行重新构建本次 main 提交，固定 Debian 13 Trixie / Linux 6.18.51；全部验证成功后由 release job 自动生成 tag，发布 `<basename>-uboot-firmware.tar` 和独立 `e87n-display` Debian 包两个公开附件。当前硬件未验收，因此发布为明确标记实验性的 **Pre-release**，不设为稳定 Latest，不执行刷写。

新固件为[未压缩 USTAR](UBOOT-FIRMWARE.md)：`sysupgrade-edgepi-e87n/{kernel,root,CONTROL}`，kernel 是 LZMA 内核 + 原始 initrd + DTB 的 FIT，root 是含 `/boot` 的 Debian ext4。`.img` / `.img.xz` 仅为中间或历史文件，不可刷写、不作为新公开附件。本地 R4 已生成并独立审计 EXIT 0，但它重新打包历史 Actions RAW，没有完整重编内核。未来从新源码完整构建的手动工作流**尚未 dispatch**，不能称作新的 GitHub job 或 Release 完成。

已知故障与复验：[2026-09-13 SSH keygen 审计误报修复](ci-keygen-fix-20260913.md)。原运行编译成功但审计失败，修复后的本地完整复验通过；历史 [run 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 的验证、独立显示包、完整镜像构建及审计已全部成功。它仅保留为历史构建证据和备用下载，不代表当前 main 已完成新构建或发布；原失败 run 的状态不会因此改变。**远端 Release 是否已成功发布尚未确认。**

## 唯一操作：无参数手动构建并发布

1. 打开 [E87N Debian 13 release](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)。
2. 点击 **Run workflow**，保持默认分支 **main**。
3. 直接点击 **Run workflow**，无需填写任何参数。

没有 tag、run ID 或内核输入框，也没有第二个发布工作流。新运行实际重建本次 main 提交，不查找或复用历史成功构建。其他分支在预检阶段拒绝；工作流必须已进入默认分支，操作者须有仓库写权限，见 [GitHub 手动运行说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。本机能通过 SSH 推送 Git，不代表 GitHub CLI 的 API 账号也有发布/dispatch 权限；不需要把个人 token 放进仓库，云端使用作业自身的 `GITHUB_TOKEN`。

`validate` 根据本次手动运行的 ID 自动计算 tag 名称 `e87n-trixie-6.18.51-<GITHUB_RUN_ID>` 并预检，不创建远端 tag。验证成功后，`image` 与 `display` 并行构建；三者全部成功后，`release` 创建远端 tag 和 Release 并上传附件。整个过程只需这一次手动运行，不会由 tag push 触发构建。更换内核必须修改并审查源码 pin、补丁、验证器和工作流。

| 构建配置 | 固定来源 |
| --- | --- |
| Debian / 桌面 | `RELEASE=trixie`、`BUILD_MINIMAL=yes`、`BUILD_DESKTOP=no` |
| Linux | `6.18.51`，`f6388029ea9e2c9e807d73827658738ea131faee` |
| Armbian | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，经现有启动器与框架准备脚本检查 |
| 额外存储栈 | CI 明确传入 `E87N_EXTRA_STORAGE=no` |
| 显示包版本 | `packaging/e87n-display/VERSION`；CI 不覆盖 `--version` |
| 系统默认值 | 由镜像定制脚本维护：root / doumao、SSH、DHCP、Asia/Shanghai、zh_CN.UTF-8；见 [默认配置](DEFAULTS.md) |

显示包 job 调用 `bash scripts/build-display-deb.sh --output-dir DIR`。镜像 job 调用现有 `build-armbian.sh` 构建无屏幕系统，审计 GPT 中间镜像，再调用 `scripts/build-factory-firmware.py --headless --image RAW --output <basename>-uboot-firmware.tar` 转换。转换器内部和 CI 再次调用的 `verify-factory-firmware.py` 都使用 `--headless`，与 TAR 的 `CONTROL` 标记对应。显示包单独发布，不预装进基础镜像。APT 软件源和 runner 没有完整快照，不能据此宣称整个固件逐字节可复现。

## Runner、资源和权限

`validate` 使用 `ubuntu-24.04`；成功后两个构建 job 并行执行：独立显示包 job 在 x64 上打包 `Architecture: all` 的用户空间包，镜像 job 在 `ubuntu-24.04-arm` 上原生编译 ARM64。没有使用 QEMU 模拟内核编译。`PREFER_DOCKER=no` 很关键：固定 Armbian 框架检测到 Docker 时原本可能改用容器，该参数令框架走原生 sudo 路径；显式 `build` 子命令避免交互式选择。

GitHub 为公开仓库列出了该 ARM64 标签、4 核、16 GB 内存及 **14 GB 存储**；其存储承诺不足以无条件保证本项目构建空间。见 [GitHub 托管 runner 规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

`ci-prepare-runner.sh` 检查 GitHub-hosted 身份、公开仓库、Ubuntu 24.04、aarch64 和 workspace。清理仅限经检查的三个预装 SDK 目录：`/usr/share/dotnet`、`/usr/local/lib/android`、`/opt/hostedtoolcache/CodeQL`，以及 APT 下载缓存。不存在的 SDK 跳过；符号链接、路径解析不符、目录自身或内部挂载均拒绝。不会清理整棵 toolcache，也不调用 Docker prune。此脚本不得用于自托管或共享 runner。

安装依赖后要求 workspace 至少有 **30 GiB 可用空间**，不足则在编译前失败，并留下 `runner.log`。这只是预检门槛，峰值还取决于内核、rootfs、压缩和 raw 审计副本。若托管 runner 无法满足，需要重新评估有足够磁盘的原生 ARM64 runner；不要直接删除门槛或扩大清理范围。检查失败时显示包 job 仍可独立完成。

全局仅有 `contents: read`，checkout 不持久保存凭证；只有成功后的 `release` job 获得 `contents: write` 和 `actions: read`，用于下载当前运行的附件并创建 tag/Release。构建与测试 job 没有仓库写权限。同一工作流/ref 使用 concurrency 分组，`cancel-in-progress: false` 保留正在运行的构建；GitHub 仍可能替换尚未开始的等待项。镜像 job 总限时 360 分钟，准备 15、构建及审计 285、收集 15、候选上传 25、独立日志上传 10 分钟，为收尾留出时间；发布 job 限时 30 分钟。

## 成功后按 tag 发布

`release` 必须等待 `validate`、`display`、`image` 全部成功；失败或取消不会进入发布。它下载**当前 run 和 attempt** 的两个准确 artifact 名称，先逐文件核对 SHA-256，再确认源码提交、run/attempt、Debian/内核/显示包版本、审计结果和退出码均匹配。缺文件、哈希不符、符号链接、越界路径或混入其他运行的产物都会失败。

Release **只上传两个二进制附件**：

- `<basename>-uboot-firmware.tar` 未压缩 USTAR 固件，已经预装显示包。
- 独立 `e87n-display_*_all.deb`，用于安装/升级屏幕控制程序。

两个下载链接和 SHA-256 直接写进 Release 正文，不需要另外下载校验文本。GitHub 自动附带的 Source code zip/tar.gz 是源码，不是额外的系统固件。内部 staging 保留内核包归档、构建证据、元数据和校验清单供发布前验证；发布器只上传固件 TAR 和显示包。原始内核包、元数据和日志仍保留在源 run 的 Actions artifacts（14 天），不作为额外安装附件发布。

release job 使用预检输出的自动 tag 名称，再检查同名 tag/Release，创建指向**本次工作流实际构建提交 `GITHUB_SHA`** 的 draft pre-release，上传明确列出的附件并核对远端文件；完成后才公开。不会给旧版本覆盖附件，也不会把 tag 指向后续漂移的 main。若 tag 冲突或上传/核对失败，作业报错，已有 draft/tag 保留供人工检查，不自动删除。需要重试发布时，重新点击 **Run workflow** 发起新的手动运行，获得新的 run ID，重新构建并自动生成新 tag。不要通过重跑失败 job 重试发布：重跑沿用原 run ID，可能仍会遇到同名 tag，且不同 attempt 的产物不会混用。发布行为参考 [GitHub CLI release create](https://cli.github.com/manual/gh_release_create)。

版本化下载在仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases)，不受 Actions artifacts 的 14 天保留期约束。新的无参数工作流尚待实际手动运行验证，远端 Release 是否已成功发布尚未确认。已成功的 `34737922588` 使用旧的 artifact-only 流程，仅作历史证据和备用下载，不会因更新工作流自动变成 Release。

另一次历史手动发布 [run 34740998259](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34740998259) 使用已移除的独立发布工作流。只读远端检查确认：来源解析、下载和发布准备均通过，但在 `gh release create` 阶段失败。旧发布器未显示具体错误，因此失败原因尚未证实；该结果不代表新工作流已经运行或 Release 已成功发布。

历史验证记录（2026-09-13，简化前版本）：当时两个工作流的 actionlint、CI ShellCheck 在 macOS 通过；工作流 19 项、发布准备 21 项、发布器 27 项、来源解析/手动发布契约 12 项，共 79 项测试在 macOS 与 ARM64 Debian 13 VM 均通过。使用 `34737922588` 的真实镜像/显示包 artifacts 完成 8 文件本地 staging，清单全数通过；发布器验证只有两个用户下载文件会上传，Release 正文的两个链接及摘要已核对。当时真实 GitHub 的显式 run ID、最近成功构建解析与只读发布预检均通过。没有调用真实发布或手动 dispatch，本地发布目录不等于远端 Release。

上述 79 项是历史结果；独立发布入口及其来源解析代码和 12 项测试已移除，不再提供选择历史构建的功能。其后的 GPT 发布版本增加真实 Shell 展开测试，覆盖不同 run ID 的自动 tag、分支限制和发布预检失败；当时工作流 20 项、发布准备 21 项、发布器 27 项，合计 68 项在 macOS 与 ARM64 Debian 13 VM 均通过，actionlint 和 CI ShellCheck 在 macOS 通过。这同样是 TAR 格式切换前的历史结果，不证明新固件工作流已通过或实际发布成功。

## 中间镜像、最终 TAR 审计与失败输出

镜像命令正常退出且发现新生成的 `.img.xz` 后，每个文件解压到 `RUNNER_TEMP` 下唯一的 `e87n-ci-audit.*` 目录，并执行：

```sh
xz -dc -- generated.img.xz > "$audit_dir/candidate.img"
sudo -n bash scripts/verify-image.sh --release trixie --require-usb-root \
  --headless --require-system "$audit_dir/candidate.img"
```

这是对本次实际中间镜像的检查，验证器使用自己分配的只读 loop 与只读挂载，不执行镜像内程序。检查包含 GPT/文件系统、历史中间 extlinux 配置/initramfs、显示/风扇、目标系统默认值等约束。随后必须转换并验证最终 TAR：

```sh
sudo -n python3 scripts/build-factory-firmware.py --headless \
  --image /path/to/candidate.img \
  --output /path/to/candidate-uboot-firmware.tar
sudo -n python3 scripts/verify-factory-firmware.py --headless /path/to/candidate-uboot-firmware.tar
```

转换在私有副本中重新整理 ext4：使用相同 UUID 的全新 735 MiB 文件系统，完整复制并比较目录树、文件 SHA、属主、模式、硬链接和 xattrs，不裁剪必要组件；原 `.img` 不变。本轮复制比较与 R4 独立最终审计均已通过，不包含硬件验收。新 root 内含 `/boot`，禁用通用 Armbian resize，改为校验完整原布局后仅扩 p5 内 ext4；在 DHCP 前只读 factory MAC，保留内核 hold。最终检查覆盖 USTAR、厂商 C 解析器、FIT/DTB 的 1 GiB/保留区、ext4/UUID、rootfs 策略与成套内核一致性；R4 还强化 Image 头/大小/对齐、FIT loadables/reservation map、root 4 KiB 块及额外 init 参数检查，详见[固件契约](UBOOT-FIRMWARE.md)。

任何解压、镜像审计、转换或最终 TAR 审计失败都使 **build 步骤失败**。依赖包含 device-tree-compiler、u-boot-tools、initramfs-tools-core、fdisk/gdisk、e2fsprogs、主机 C 编译器、zstd、libcrypt1 和常规 Linux 工具。整个 TAR 必须 `<=768 MiB`，此静态政策不证明 Web 可用 RAM 足够。

`image.log` 保存完整构建/审计控制台，`image-audit-N.log` 保存中间镜像审计，`factory-firmware-audit-1.log` 保存最终固件审计，`image.exit-code` 保存退出码。新流程从 `output/ci/firmware/` 收集 TAR，不上传 raw 或 `.img.xz` 中间镜像。脚本拒绝已有 Armbian/固件输出，避免混入旧文件；临时文件保留在一次性构建环境中供诊断。

每个 job 的收集及上传步骤使用 `always()`，普通失败后也尝试保留已经产生的 TAR、包、日志和校验清单。原始 CI/框架日志另有独立上传步骤，所以收集失败不会阻断日志上传。机器失联、整体硬超时、强制取消、磁盘彻底耗尽或 GitHub artifacts 服务失败时，上传仍可能无法完成。关于失败与取消条件，见 [GitHub 状态检查函数](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions)。

下载对应 run / attempt 的候选 artifact 后，内部布局为：

```text
SHA256SUMS
build-metadata.json
images/                 -uboot-firmware.tar（失败时可能缺失或未通过全部检查）
packages/armbian/        内核、DTB、BSP 等包，保留子目录
packages/display/       独立显示包 job 的 .deb
logs/ci/                CI、退出码和实际镜像审计日志
logs/armbian/           框架日志（镜像 job）
```

两个 job 的候选 artifact 分开保存，并非单个 artifact 同时包含所有目录。名称包含 `github.run_id` 和 `github.run_attempt`，保留 14 天；上传采用零额外压缩，固件自身仍是未压缩 USTAR。下载行为和保留限制见 [upload-artifact 官方说明](https://github.com/actions/upload-artifact/tree/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)。

在解压后的 artifact 根目录运行 `sha256sum --check SHA256SUMS`（macOS 可用 `shasum -a 256 -c SHA256SUMS`）。同时检查 `build-metadata.json` 的 `build_step_outcome`、`image_static_audit`、`factory_format=e87n-uboot-firmware-tar-v1`、`factory_static_audit` 和 `collection_errors`；只有两层审计通过才满足新交付契约。校验正确只证明文件与清单一致；失败 job 的部分文件同样可以有正确校验值。元数据保留硬件待验收状态。

本轮原生 VM 已报告 902 dry-run、factory 24 项、root adapter 24 项及完整 ext4 复制比较通过；R4 TAR 独立最终审计 EXIT 0。完整 Linux `ci-regressions.sh` 已在磁盘临时目录全套通过，`regressions-disk.log` EXIT 0；此前缺 docs、AppleDouble 和 `/tmp` 满失败已解决，失败记录保留。静态 CI 85 项再次通过后，新增的 runtime/root preparer 两个编译检查目标也已复验通过。主机导出及 SHA-256 比对已完成，V3 已废弃，未来工作流未 dispatch，远端发布未确认。原生打包不等于新 GitHub job 完成，更不代表硬件验收；刷写条件见[首启准备](first-boot.md)。

收集器仅收集已知输出目录和扩展名，拒绝符号链接，使用硬链接避免复制多 GB 镜像；跨文件系统时才复制。它不会上传缓存、`.tmp` rootfs、环境变量转储、私钥或设备采集目录，也不会覆盖已存在的收集目录。

## 验证工作流本身

`validate` 安装 ShellCheck、PyYAML、Pillow、DejaVu 字体、设备树工具、GNU patch、压缩工具、dpkg 和 Debian 服务助手。actionlint 通过 Go 固定安装 `v1.7.12`，见 [actionlint 安装说明](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/install.md)。

`ci-validate.sh` 执行唯一工作流 `build-e87n.yml` 的 actionlint、所有 `ci-*.sh` 的 Bash 语法/ShellCheck、`test-ci-workflow.py` 的工作流契约和模拟构建/收集测试，以及 `test-ci-prepare-release.py` / `test-ci-publish-release.py` 的发布打包与模拟 GitHub 测试。这部分不启动镜像或软件包构建，不调用真正的 sudo，也不会发布任何 Release。

`ci-regressions.sh` 执行 hardware、display、doctor、network-policy、display-fan 验证器和 shell 启动器/产物/initramfs/镜像/LTS/采集器夹具。系统 rootfs 夹具明确使用 `sudo -n python3 -B tests/test-verify-system.py`；显示包测试同样以 sudo 运行，以覆盖临时 `dpkg --root` 中的安装、升级、删除、重装与 purge。包测试会生成测试包并模拟运行时服务命令，不安装到 runner 主系统。新增 `test-image-defaults.py` 或 `.sh` 后也会被纳入。每套测试独立保存日志，失败后继续收集其他测试结果，但 validation job 最终失败。依赖真实框架 checkout 的测试和实际系统 smoke 测试不属于此 fixture job。

本地仅检查 CI 代码，先安装 actionlint、ShellCheck 和 PyYAML 后运行：

```sh
bash scripts/ci-validate.sh
# 缺少 PyYAML 时可使用隔离环境：
uv run --with 'PyYAML==6.0.3' bash scripts/ci-validate.sh
```

完整回归需要 Linux、上述系统依赖及免交互 sudo：`bash scripts/ci-regressions.sh`。在 macOS 上执行可移植子集不能代替 Linux root-owned 与 dpkg 生命周期测试。CI 夹具、完整镜像审计和上板测试的结论应分别记录。

## Actions 提交审核记录

2026-09-13 从 action 官方仓库核对以下 release/tag 到提交的映射，并审阅 `action.yml`、入口和本工作流使用的上传/checkout 行为。均使用完整提交 SHA；没有第三方发布 action。审核范围不等同于依赖供应链的完整安全审计。GitHub 推荐完整 SHA 固定，见 [安全使用参考](https://docs.github.com/en/actions/reference/security/secure-use)。

| Action | 版本 | 固定提交 |
| --- | --- | --- |
| [actions/checkout](https://github.com/actions/checkout/releases/tag/v7.0.1) | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| [actions/upload-artifact](https://github.com/actions/upload-artifact/releases/tag/v7.0.1) | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |

这两个 action 使用 Node 24，由当前 GitHub 托管 runner 提供运行支持。升级时应重新核对上游提交和接口，同时更新 YAML、工作流契约测试和本表。
