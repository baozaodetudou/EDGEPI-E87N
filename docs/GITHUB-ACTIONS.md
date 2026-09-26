# GitHub Actions：独立 Firmware 与 Display 发布

本仓库使用两个互相独立的生产发布通道。它们都只响应 `workflow_dispatch`；push、Pull
Request、tag push 和定时任务不会自动发布，也不会自动刷写设备。

- [**Firmware workflow / Release**](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)：
  构建和验证完整 Debian 固件，只发布
  `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar`。
- [**Display workflow / Release**](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-display.yml)：
  构建和验证显示软件包，只发布
  `e87n-display_<version>_all.deb`。

两个 workflow 使用独立的 run、tag、Release、版本和 SHA-256。Display 发布失败不会阻止
已经验证的 Firmware Release；发布新版 display 也不要求重建 firmware。

## 触发 Firmware 发布

1. 确认 `userpatches/config/e87n-build.json` 中的 `firmware_version` 是希望公开的版本。保持同一
   版本重跑会在全部构建和验收通过后替换同名 Image Release/tag；需要保留旧版时先递增版本。
2. 打开 [E87N Debian 13 ARM64 固件构建与发布](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)。
3. 点击 **Run workflow**，分支保持 `main`，按工作流页面要求确认并启动。
4. 等待固件检查、镜像构建、静态审计、QEMU 验收和 release 阶段全部成功。
5. 打开 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases)，进入对应的
   Image Release，下载唯一的项目二进制附件
   `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar`。

Firmware workflow 的逻辑边界为：

```text
validate
  └─ CI/YAML/Bash/Python、固件、rootfs、网络和用户空间回归

image
  ├─ 从当前源码生成基线 e87n-display 包并预装到 rootfs
  ├─ 原生 ARM64 构建 Debian 镜像
  ├─ 静态镜像审计
  ├─ U-Boot firmware TAR 转换与审计
  ├─ RAM 诊断产物
  └─ 使用同源基线 display 包完成 Docker/QEMU 验收

release
  ├─ 校验源码提交、run、SHA-256 和审计结果
  └─ 创建 Image Release，只上传版本化的 U-Boot firmware TAR
```

这里的 display 包是固件构建输入和 QEMU 验收基线，用于证明固件内预装版本可安装、可启动
并满足包生命周期要求。它不是 Firmware Release 的第二个下载附件，也不要求与以后由
Display workflow 发布的 deb 保持同一 run、tag 或 SHA-256。

## 触发 Display 发布

1. 修改显示程序或打包内容后，先递增 `packaging/e87n-display/VERSION`；同一 Debian 包版本
   不允许发布不同内容。
2. 打开 [E87N 显示 deb 快速构建与发布](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-display.yml)，点击 **Run workflow**。
3. 等待源码检查、软件包构建和安装/升级/重装/卸载生命周期测试全部成功。
4. 打开对应的 Display Release，下载唯一的项目二进制附件
   `e87n-display_<version>_all.deb`。

Display workflow 的逻辑边界为：

```text
validate
  └─ 显示源码、配置、打包和版本检查

package
  ├─ 构建 e87n-display_<version>_all.deb
  └─ 验证安装、升级、重装、配置保留、卸载和重新安装

release
  ├─ 校验包版本、源码提交和 SHA-256
  └─ 创建 Display Release，只上传 e87n-display_<version>_all.deb
```

Display 可以在兼容的已运行固件上高频升级。只有显示功能开始依赖新的内核、DTB 或其他
固件能力时，才需要在 Release 正文明确最低兼容 Firmware 版本；普通用户空间更新不需要
重新构建或刷写固件。

两个通道使用固定、可读且不会因重跑改变的发布身份：

| 通道 | 版本来源 | Tag | Release 标题 |
| --- | --- | --- | --- |
| Image | `e87n-build.json: firmware_version` | `e87n-image-v<firmware_version>` | `E87N Image \| <firmware_version> \| Debian ... \| Linux ...` |
| Display | `packaging/e87n-display/VERSION` | `e87n-display-v<display_version>` | `E87N Display \| <display_version>` |

Actions `run_id` 和 `run_attempt` 只保留在候选 artifact、构建元数据和 Release 正文中，确保
证据精确绑定本次运行，但不会进入正式 tag。Image `preflight` 对同名 tag、已发布 Release 或
残留 draft 只做只读识别；构建、审计和 release 输入校验全部通过后，发布器先用本次 run
专属临时 tag 创建 draft、上传并核对远端 SHA-256，然后才删除旧 Image Release/tag，将已
验证 draft 切换为正式 tag。同版本因此只保留一份当前公开镜像。切换中途失败时工作流停止
且不自动回滚，应同时检查正式 tag 和 `-replacement-<run>-<attempt>` 临时 tag 后重跑。

Display 不启用覆盖参数：同版本已有 tag、Release 或 draft 时 preflight 仍会失败，必须递增
`packaging/e87n-display/VERSION`，或由维护者明确处理残留的失败 draft。

## Release 中的文件

| Release 通道 | 唯一的项目二进制附件 | 用途 |
| --- | --- | --- |
| Image | `edgepi-e87n-debian_<firmware_version>_arm64-uboot-firmware.tar` | 上传到原厂 U-Boot Web 的 plain firmware / `firmware` 入口 |
| Display | `e87n-display_<version>_all.deb` | 在已经启动的 Debian 中安装或升级屏幕服务 |

每个 Release 正文只为本通道附件提供 SHA-256、源码提交、版本和验收边界。GitHub 自动显示
的 Source code zip/tar.gz 不是项目发布的固件或 deb。内核包、构建日志、RAM 诊断和 QEMU
结果保留在对应 Actions artifact 中，供排查使用。

## 成功标准

Firmware Release 需要同时满足：

- 固件相关检查和原生 ARM64 镜像构建成功；
- firmware TAR 静态审计通过；
- 固件内预装的同源基线 display 包完成 QEMU/systemd/SSH/DHCP/APT 验证；
- 发布阶段重新核对 TAR 的 SHA-256、源码提交和本次 run 证据。

Display Release 需要同时满足：

- 版本来自 `packaging/e87n-display/VERSION`，包名和元数据正确；
- 显示源码、配置及软件包测试通过；
- 安装、升级、重装、配置保留、卸载和重新安装生命周期通过；
- 发布阶段重新核对 deb 的 SHA-256、版本、源码提交和本次 run 证据。

workflow “运行成功”与 Release “已发布”仍是两个阶段。某一通道发布失败时，只排查该通道
的日志和 failure evidence，不要从失败 run 中取部分 artifact 作为正式下载。

## 权限与安全

- 普通 job 只有 `contents: read`；每个通道只有自己的 `release` job 拥有 `contents: write`。
- 同名 Image Release/tag 的删除只发生在最终 `release` job，且在 replacement draft 的附件
  SHA-256 已远端复验后；构建、审计、验收或 replacement 上传失败不会删除当前公开镜像。
- checkout 和 upload-artifact 使用固定提交 SHA，而不是可变 tag。
- 不读取 SSH 私钥、设备密码或工作区外敏感文件。
- 构建明确禁用写卡、外部服务器推送和自动刷写。
- 两个通道当前都标记为 Pre-release 且不争用仓库级 `latest`；软件验证通过不等于 E87N
  实机完整验收通过。GitHub 只有一个全仓库 latest，不能分别代表 Image 和 Display 通道。

旧规则生成的 `e87n-trixie-...-<run_id>`、`e87n-display-...-<run_id>` tag 及早期
双附件 Release 保留为历史记录。覆盖逻辑只处理当前中央配置精确生成的
`e87n-image-v<firmware_version>`；不会改写旧规则的 tag，也不会覆盖 Display tag。

## 本地验证工作流

Linux 环境可执行：

```sh
bash scripts/ci-validate.sh image
bash scripts/ci-regressions.sh
bash scripts/ci-validate.sh display
bash scripts/ci-display-regressions.sh
```

macOS 可先执行 YAML、Python 和便携脚本检查；完整镜像、loop 挂载、root-owned dpkg
生命周期和原生 ARM64 构建应交给 Actions 或 Linux 构建机完成。
