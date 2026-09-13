# 2026-09-13 镜像审计误报修复

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

这是一份**本地重新审计**记录，不会改变原 Actions 的失败结论或 artifact 元数据，也不等于新的云端构建成功。修复后的新 run 需另外记录实际结果。实体 E87N 启动、网口 DHCP、屏幕显示及风扇散热仍未验收；不得整盘覆盖现有 eMMC。
