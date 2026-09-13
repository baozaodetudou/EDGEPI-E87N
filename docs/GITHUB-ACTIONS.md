# GitHub Actions 手动构建与 tag 发布

工作流位于 [build-e87n.yml](../.github/workflows/build-e87n.yml)。**只接受手动 `workflow_dispatch`；push、tag push、PR 和定时任务均不触发。** 手动运行后，生成 Debian 13 Trixie 最小镜像和独立 `e87n-display` Debian 包，全部验证成功才以指定 tag 发布到 GitHub Releases。当前硬件未验收，因此发布为明确标记实验性的 **Pre-release**，不设为稳定 Latest，不执行刷写。

已知故障与复验：[2026-09-13 SSH keygen 审计误报修复](ci-keygen-fix-20260913.md)。原运行编译成功但审计失败，修复后的本地完整复验通过；新 [run 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 的验证、独立显示包、完整镜像构建及审计已全部成功。原失败 run 的状态不会因此改变。

## 触发和输入

从 Actions → **E87N Debian 13 release** → **Run workflow**，选择 **main** 后手动运行。其他分支在预检阶段拒绝；工作流必须已进入默认分支，操作者须有仓库写权限，见 [GitHub 手动运行说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。本机能通过 SSH 推送 Git，不代表 GitHub CLI 的 API 账号也有发布/dispatch 权限；不需要把个人 token 放进仓库，云端使用作业自身的 `GITHUB_TOKEN`。

手动输入有两个：

- `kernel_version`：只有 `6.18.51` 的 choice，脚本也拒绝其他值。
- `release_tag`：可填新的版本，例如 `v2026.09.13-1`；留空则生成 `e87n-trixie-6.18.51-<run-id>`。允许字母、数字、点、下划线、连字符等受限安全格式，不接受路径、空白或命令文本。已有同名 tag 或 Release 会在构建前拒绝；发布前再检查一次，绝不覆盖旧版本。

更换内核必须修改并审查源码 pin、补丁、验证器和工作流；没有任意构建命令或发行版输入。自动生成 tag 仅发生在这次手动运行内，不是 tag push 触发构建。

| 输入 | 固定来源 |
| --- | --- |
| Debian / 桌面 | `RELEASE=trixie`、`BUILD_MINIMAL=yes`、`BUILD_DESKTOP=no` |
| Linux | `6.18.51`，`f6388029ea9e2c9e807d73827658738ea131faee` |
| Armbian | `7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，经现有启动器与框架准备脚本检查 |
| 额外存储栈 | CI 明确传入 `E87N_EXTRA_STORAGE=no` |
| 显示包版本 | `packaging/e87n-display/VERSION`；CI 不覆盖 `--version` |
| 系统默认值 | 由镜像定制脚本维护：root / doumao、SSH、DHCP、Asia/Shanghai、zh_CN.UTF-8；见 [默认配置](DEFAULTS.md) |

显示包 job 调用 `bash scripts/build-display-deb.sh --output-dir DIR`。镜像 job 调用现有 `build-armbian.sh`，由其复制 overlay 后在目标 chroot 构建并安装显示包；因此 Linux 镜像启动器不依赖主机先生成 `.deb`。两个 job 使用同一仓库提交和 VERSION，但打包环境与时间可能不同，不承诺两个包逐字节相同。APT 软件包源和 runner 软件也没有完整快照，不能据此宣称整个镜像逐字节可复现。

## Runner、资源和权限

`validate` 使用 `ubuntu-24.04`；成功后，独立显示包 job 在 x64 上打包 `Architecture: all` 的用户空间包，镜像 job 在 `ubuntu-24.04-arm` 上原生编译 ARM64。没有使用 QEMU 模拟内核编译。`PREFER_DOCKER=no` 很关键：固定 Armbian 框架检测到 Docker 时原本可能改用容器，该参数令框架走原生 sudo 路径；显式 `build` 子命令避免交互式选择。

GitHub 为公开仓库列出了该 ARM64 标签、4 核、16 GB 内存及 **14 GB 存储**；其存储承诺不足以无条件保证本项目构建空间。见 [GitHub 托管 runner 规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

`ci-prepare-runner.sh` 检查 GitHub-hosted 身份、公开仓库、Ubuntu 24.04、aarch64 和 workspace。清理仅限经检查的三个预装 SDK 目录：`/usr/share/dotnet`、`/usr/local/lib/android`、`/opt/hostedtoolcache/CodeQL`，以及 APT 下载缓存。不存在的 SDK 跳过；符号链接、路径解析不符、目录自身或内部挂载均拒绝。不会清理整棵 toolcache，也不调用 Docker prune。此脚本不得用于自托管或共享 runner。

安装依赖后要求 workspace 至少有 **30 GiB 可用空间**，不足则在编译前失败，并留下 `runner.log`。这只是预检门槛，峰值还取决于内核、rootfs、压缩和 raw 审计副本。若托管 runner 无法满足，需要重新评估有足够磁盘的原生 ARM64 runner；不要直接删除门槛或扩大清理范围。检查失败时显示包 job 仍可独立完成。

全局仅有 `contents: read`，checkout 不持久保存凭证；只有成功后的 `release` job 获得 `contents: write` 和 `actions: read`，用于下载当前运行的附件并创建 tag/Release。构建与测试 job 没有仓库写权限。同一工作流/ref 使用 concurrency 分组，`cancel-in-progress: false` 保留正在运行的构建；GitHub 仍可能替换尚未开始的等待项。镜像 job 总限时 360 分钟，准备 15、构建及审计 285、收集 15、候选上传 25、独立日志上传 10 分钟，为收尾留出时间；发布 job 限时 30 分钟。

## 成功后按 tag 发布

`release` 必须等待 `validate`、`display`、`image` 全部成功；失败或取消不会进入发布。它下载**当前 run 和 attempt** 的两个准确 artifact 名称，先逐文件核对 SHA-256，再确认源码提交、run/attempt、Debian/内核/显示包版本、审计结果和退出码均匹配。缺文件、哈希不符、符号链接、越界路径或混入其他运行的产物都会失败。

Release 附件包括：

- 完整 `.img.xz` 镜像与独立 `e87n-display_*_all.deb`。
- `kernel-packages.tar.xz`：匹配的内核、DTB、BSP 等 Debian 包。
- `build-evidence.tar.xz`：两个原始 artifact 的元数据、清单和构建/审计日志。
- `image-build-metadata.json`、`display-build-metadata.json`、`RELEASE-NOTES.md` 和顶层 `SHA256SUMS`。

先创建指向**实际构建提交 SHA** 的 draft pre-release，再上传明确列出的附件并核对远端文件；完成后才公开。不会给旧版本覆盖附件，也不会把 tag 指向后续漂移的 main。若上传/核对失败，作业报错并保留 draft/tag 供人工检查，不自动删除或假装成功；下次完整重建应使用新 tag，已发布版本保持原样。发布行为参考 [GitHub CLI release create](https://cli.github.com/manual/gh_release_create)。

版本化下载在仓库 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases)，不受 Actions artifacts 的 14 天保留期约束。首次手动发布 job 尚待实际运行验证；已成功的 `34737922588` 使用旧的 artifact-only 流程，不会因更新工作流自动变成 Release。

2026-09-13 本地验证：actionlint、ShellCheck 通过；工作流 19 项、发布附件准备 21 项、发布器 27 项测试在 macOS 与 ARM64 Debian 13 VM 均通过。使用 `34737922588` 的真实镜像/显示包 artifacts 完成 8 个本地发布附件准备，清单全数通过，内核包与证据归档的成员已核对；发布器的实际文件契约和 GitHub **只读**预检也通过。没有调用真实发布或手动 dispatch，本地发布目录不等于远端 Release。

## 实际镜像审计与失败输出

镜像命令正常退出且发现新生成的 `.img.xz` 后，每个文件解压到 `RUNNER_TEMP` 下唯一的 `e87n-ci-audit.*` 目录，并执行：

```sh
xz -dc -- generated.img.xz > "$audit_dir/candidate.img"
sudo -n bash scripts/verify-image.sh --release trixie --require-usb-root \
  --require-display-fan --require-system "$audit_dir/candidate.img"
```

这是对本次实际镜像的检查，验证器使用自己分配的只读 loop 与只读挂载；不执行镜像内程序。检查包含 GPT/文件系统、启动配置/initramfs、显示/风扇、目标系统默认值等现有验证器约束。任意解压或审计失败都会使 **build 步骤失败**，不会先把编译成功视为整个构建通过。依赖包含 device-tree-compiler、u-boot-tools、initramfs-tools-core、fdisk/gdisk、e2fsprogs、zstd、libcrypt1 和常规 Linux 工具。

`image.log` 保存完整构建/审计控制台，`image-audit-N.log` 保存独立审计结果，`image.exit-code` 保存退出码。临时 raw 镜像不上传，留在一次性 VM 直到作业销毁；不会为清理它而强制卸载未知挂载。脚本拒绝已有 `source/armbian-build/output`，避免将旧镜像当成本次成功结果。

每个 job 的收集及上传步骤使用 `always()`，普通失败后也尝试保留已经产生的镜像、包、日志和校验清单。原始 CI/框架日志另有独立上传步骤，所以收集失败不会阻断日志上传。机器失联、整体硬超时、强制取消、磁盘彻底耗尽或 GitHub artifacts 服务失败时，上传仍可能无法完成。关于失败与取消条件，见 [GitHub 状态检查函数](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions)。

下载对应 run / attempt 的候选 artifact 后，内部布局为：

```text
SHA256SUMS
build-metadata.json
images/                 镜像 job 已生成的镜像（失败时可能不完整）
packages/armbian/        内核、DTB、BSP 等包，保留子目录
packages/display/       独立显示包 job 的 .deb
logs/ci/                CI、退出码和实际镜像审计日志
logs/armbian/           框架日志（镜像 job）
```

两个 job 的候选 artifact 分开保存，并非单个 artifact 同时包含所有目录。名称包含 `github.run_id` 和 `github.run_attempt`，保留 14 天；使用零额外压缩上传已经压缩的镜像和包。下载行为和保留限制见 [upload-artifact 官方说明](https://github.com/actions/upload-artifact/tree/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)。

在解压后的 artifact 根目录运行 `sha256sum --check SHA256SUMS`（macOS 可用 `shasum -a 256 -c SHA256SUMS`）。同时检查 `build-metadata.json` 的 `build_step_outcome`、`image_static_audit` 和 `collection_errors`。校验正确只证明下载文件与清单一致；失败 job 的部分文件同样可以有正确校验值。元数据明确保留硬件待验收状态。

收集器仅收集已知输出目录和扩展名，拒绝符号链接，使用硬链接避免复制多 GB 镜像；跨文件系统时才复制。它不会上传缓存、`.tmp` rootfs、环境变量转储、私钥或设备采集目录，也不会覆盖已存在的收集目录。

## 验证工作流本身

`validate` 安装 ShellCheck、PyYAML、Pillow、DejaVu 字体、设备树工具、GNU patch、压缩工具、dpkg 和 Debian 服务助手。actionlint 通过 Go 固定安装 `v1.7.12`，见 [actionlint 安装说明](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/install.md)。

`ci-validate.sh` 执行 actionlint、所有 `ci-*.sh` 的 Bash 语法/ShellCheck、`test-ci-workflow.py` 的工作流契约和模拟构建/收集测试，以及 `test-ci-prepare-release.py` / `test-ci-publish-release.py` 的发布打包与模拟 GitHub 测试。这部分不启动镜像或软件包构建，不调用真正的 sudo，也不会发布任何 Release。

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
