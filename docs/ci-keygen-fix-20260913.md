# 2026-09-13 镜像审计误报修复

**修复后已全绿：[Actions 34737922588](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588)。** 本轮镜像与独立显示包已下载并完成校验；精确文件信息见本文“修复后的云端产物”。这不是实体板卡验收或 GitHub Release 发布记录。

失败运行：[Actions 34735890073](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34735890073)，源码 `d075699b14fa5af8ad2fb334250e11ee72f417f6`。

## 原因与修复

`validate`、独立显示包和镜像编译/压缩已成功；镜像 job 在最终 `verify-system.py` 审计处失败：`SSH host-key generation base unit is missing its command`。

Debian 13 的 `sshd-keygen.service` 使用 `ExecStart=ssh-keygen -A`。检查器原先只接受 `/usr/bin/ssh-keygen -A`，属于验证器误报，并非缺少 ssh-keygen。systemd 允许在标准搜索路径内使用不带斜杠的程序名，见 [Debian systemd.service 文档](https://manpages.debian.org/bookworm/systemd/systemd.service.5.en.html#COMMAND_LINES)。

修复仅涉及验证器和测试，没有改动镜像配方、SSH 安全设置或关闭审计。验证器接受 `[Service]` 中唯一的上述两种命令，并检查镜像内 ssh-keygen 是 root 所有、不可被组/其他用户写入的可执行普通文件；缺少命令、额外命令、错误参数、shell 包装和服务 mask 仍不能通过。

新增真实 Debian 13 OpenSSH 服务文件夹具，替代原来人为构造的绝对路径夹具；同时覆盖 merged-/usr 服务链接、sshd 别名、sysinit 下的 timesyncd 和 locale.conf 迁移链接。系统审计套件从 20 项增至 27 项（其中包括多组负例），在 ARM64 Debian 13 VM 中通过。

## 对失败运行的实际产物复验

下载失败运行保留的镜像 artifact，全部 `SHA256SUMS` 校验通过。镜像文件为：

```text
Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
SHA256: 74c291ff89614b73485b98314914b75f6a1a215f905651db42c3ea04d65351a3
```

传入隔离 ARM64 Linux VM 后再次核对压缩文件 SHA-256，解压并使用修复后的源码执行：

```sh
sudo bash scripts/verify-image.sh --release trixie --require-usb-root \
  --require-display-fan --require-system /tmp/e87n-actions-34735890073.img
```

完整命令退出 0：GPT、两分区只读 fsck、Debian 13、内核/模块/PHY 固件、DTB、启动 UUID、屏幕/风扇静态配置、系统默认值和 USB-root/initramfs 检查全部通过。未修改下载镜像，未执行镜像内程序，没有访问或修改实体设备。

以上是一份**本地重新审计**记录，不会改变原 Actions 的失败结论或 artifact 元数据，也不等于新的云端构建成功。修复后新 run 的独立结果见下文。实体 E87N 启动、网口 DHCP、屏幕显示及风扇散热仍未验收；不得整盘覆盖现有 eMMC。

## 补充用户空间与回归测试

修复提交为 `2a60011f98d33b9cc38c005061ec8935e029874c`。对该提交的干净源码副本，在原生 ARM64 Debian 13 VM 执行完整 `scripts/ci-regressions.sh`，退出 0；新云端运行的 `validate` 也已通过。

另外，以失败运行的上述实际镜像为输入，再次执行 `tests/smoke-minimal-userspace.sh`，退出 0。测试只在可丢弃 bootfs/rootfs 副本应用同一配方，确认签名 `apt update`、安装并执行 `hello`、中文 UTF-8、修复后的系统静态审计、隔离 loopback 中的真实 root 密码 SSH/PAM 登录，以及 SSH host keys 的唯一性和重复生成持久性。输入镜像测试前后哈希相同。

这些检查不启动目标内核或 systemd PID 1，不证明物理网口 DHCP、E87N 屏幕/风扇或整机首启时序正常。

## 修复后的云端产物

[Actions 34737922588 / attempt 1](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/34737922588) 对修复提交 `2a60011f98d33b9cc38c005061ec8935e029874c` 重新完整构建。`validate`、`display`、`image` 三个 job 全部成功；镜像 job 于 2026-09-13 12:45:32 Asia/Shanghai 完成，包含镜像构建、静态审计、收集和上传。本记录在构建后补记；随后文档和手动发布流程的改动没有重新编译或改变这份二进制。

固定目标为 Debian 13 Trixie / Linux 6.18.51，`E87N_EXTRA_STORAGE=no`，显示包 `1.0.0-1`。下载入口与 artifact 名称见 [DOWNLOADS.md](DOWNLOADS.md)。镜像文件名仍为前文同名文件，**不能混用失败 run 或历史候选的哈希**：

```text
压缩大小：181760648 字节（约 173.34 MiB）
压缩 SHA-256：0c48cb26e0c3e5074756cb15cebb3778b134f6a5bb1e4d27a12ab7a98bff3607
解压大小：1174405120 字节（1120 MiB）
解压 SHA-256：289f766db8e0a74993e36a4a776abf39a1b380d56333b8875e1586864b05f5c9

独立显示包：e87n-display_1.0.0-1_all.deb
大小：24592 字节
SHA-256：a483a21826ce14b4d0f6873c3c88fa2ae6633c802fc23f435234e87479261ec0
```

本机下载后的镜像 artifact 14 个文件、显示包 artifact 4 个文件全部通过各自 `SHA256SUMS`；完整 xz 解压流校验通过，得到以上 raw SHA-256，没有写入物理介质。镜像元数据为 `build_step_outcome=success`、`image_static_audit=passed`、`collection_errors=[]`，`image.exit-code=0`。

最终审计日志包含 GPT/ext4、Debian/内核/PHY 固件、DTB/UUID、屏幕/风扇、系统默认值和 initramfs/USB-root 检查的 PASS。实际 root UUID 为 `ee0d711d-5cce-4fa5-9425-50d0bcb26096`。内核 Image、DTB 和 config 的哈希与首轮相同，符合本次只修复检查器、不修改内核或镜像配方的范围；整盘镜像因构建时间、UUID 等不同而具有新哈希。

源码、云端构建、静态检查和下载校验已对应起来；硬件状态依然是 `pending`。镜像没有通过 LuCI 或 eMMC 整盘刷写，本轮也没有修改原设备 OpenWrt。
