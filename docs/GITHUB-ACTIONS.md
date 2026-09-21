# GitHub Actions：手动构建并发布

本仓库只有一个生产工作流：

[E87N Debian 13 手动构建与发布](https://github.com/baozaodetudou/EDGEPI-E87N/actions/workflows/build-e87n.yml)

它只响应 `workflow_dispatch`。push、Pull Request、tag push 和定时任务都不会自动消耗
构建资源，也不会自动刷写或发布。

## 一键操作

1. 打开上面的 Actions 页面。
2. 选择左侧工作流 **E87N Debian 13 手动构建与发布**。
3. 点击右上角 **Run workflow**。
4. 分支保持 `main`，不填写任何输入，点击绿色 **Run workflow**。
5. 等待 `validate`、`display`、`image` 全部成功，再查看 `release`。
6. 打开 [Releases](https://github.com/baozaodetudou/EDGEPI-E87N/releases)，下载最新的 Pre-release。

工作流没有输入框。版本、Debian 版本、内核版本、Armbian 提交和独立显示包版本都从
仓库固定配置读取；每次点击都会构建当时 `main` 的实际提交。

## 工作流做什么

```text
validate
  ├─ CI/YAML/Bash/Python 契约检查
  ├─ 用户空间、显示、固件、rootfs、网络和包生命周期回归
  └─ 生成本次运行唯一 tag，拒绝已存在 tag/Release

display ── 构建并收集 e87n-display_<版本>_all.deb

image ─── 原生 ARM64 构建 Debian 镜像
           ├─ 静态镜像审计
           ├─ U-Boot firmware TAR 转换与审计
           ├─ RAM 诊断产物
           └─ 同一 firmware + 同一 display deb 的 Docker/QEMU 验收

release ─ 只在以上三条都成功后
           ├─ 下载本次 run 的精确 artifact
           ├─ 校验源码提交、run ID、attempt、SHA-256 和审计结果
           └─ 创建并发布 Pre-release
```

发布 tag 形如：

```text
e87n-trixie-6.18.52-<run_id>-<run_attempt>
```

run ID 和 attempt 让每次手动运行都能得到新 tag。失败重试建议重新点击 **Run workflow**，
而不是重跑旧 run 的 release job；这样会重新编译并生成全新的产物绑定关系。

## Release 中的文件

每个成功 Release 只发布两个面向用户的二进制附件。主题、页面轮换和默认配置会随本次
`main` 提交一起进入固件，并同步进入独立的 deb：

| 文件 | 用途 |
| --- | --- |
| `*-uboot-firmware.tar` | 上传到原厂 U-Boot Web 的 plain firmware / `firmware` 入口 |
| `e87n-display_<版本>_all.deb` | 已启动 Debian 系统中的屏幕服务安装/升级 |

Release 正文包含两个文件的 SHA-256、系统版本、内核 release、源码提交和硬件验收边界。
内核包、构建日志、RAM 诊断和 QEMU 结果保留在 Actions artifact 中，供排查使用；它们
不是额外的刷写文件。

## 成功标准

只有同时满足下列条件才会出现已发布 Release：

- `validate` 成功；
- 显示包构建成功，版本来自 `packaging/e87n-display/VERSION`；
- 镜像 job 在原生 ARM64 runner 上成功；
- firmware TAR 静态审计通过；
- 同一 firmware 和同一显示包完成 QEMU/systemd/SSH/DHCP/APT 生命周期验证；
- 发布脚本重新核对 artifact 的 SHA-256、源码提交、run ID 和 attempt。

GitHub 页面上的 workflow “运行成功”与 Release “已发布”是两个阶段；如果 release 失败，
先下载失败 job 的日志和 failure evidence，不要拿部分 artifact 刷机。

## 权限与安全

- 普通 job 只有 `contents: read`；只有最后的 `release` job 拥有 `contents: write`。
- checkout 和 upload-artifact 使用固定提交 SHA，而不是可变 tag。
- 不读取 SSH 私钥、设备密码或工作区外敏感文件。
- 构建明确禁用写卡、外部服务器推送和自动刷写。
- Release 标记为 Pre-release；软件验证通过不等于 E87N 实机完整验收通过。

## 本地验证工作流

Linux 环境可执行：

```sh
bash scripts/ci-validate.sh
bash scripts/ci-regressions.sh
```

macOS 可先执行 YAML、Python 和便携脚本检查；完整镜像、loop 挂载、root-owned dpkg
生命周期和原生 ARM64 构建应交给 Actions 或 Linux 构建机完成。
