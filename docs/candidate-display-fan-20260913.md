# E87N 屏幕 / 风扇候选 — 2026-09-13

**Debian 13.6 Trixie + Linux 6.18.51-current-filogic。完整构建、真实镜像只读静态验收已通过，尚未在 E87N 上启动或测试。** 本轮未刷写、重启或修改用户的 OpenWrt 设备；不能保证屏幕已点亮、风扇已起转或全部驱动正常。

这是纯 Debian/Armbian 用户空间。OpenWrt 参考项目仅提供板级硬件与 GPL 驱动移植依据，不作为目标系统。没有复制 LuCI/UCI/procd 或原有 musl 显示程序，也没有新增 Web 管理端口。

## 新版功能

- NV3007 fbtft 驱动、428×142 RGB565 显示；概览、温度/风扇、网络计数、NVMe 温度四页，默认每 2 秒刷新。
- 背光默认 20%，可切换页面、调整亮度、开关屏幕，设置持久化；无指标时显示 `--`，不编造 RPM。
- 内核独占风扇控制：PWM 四档 0/128/192/255，50/65/75℃触发 1/2/3 档，迟滞 2℃。这是待实机验证的策略，不是芯片额定温度保证。
- 显示服务不写风扇、thermal 或 PWM 控制节点；关屏不停止自动温控。当前不提供任意手动风扇百分比、停扇或 LuCI 同款网页。

在**新 Armbian 已成功启动后**使用，不能让当前 OpenWrt 执行：

```sh
e87nctl status
e87nctl fan status
sudo e87nctl display brightness 20
sudo e87nctl display screen thermal
sudo e87nctl display off
sudo e87nctl display on
```

页面名为 `overview|thermal|network|storage`，默认配置在 `/etc/e87n/display.json`，服务为 `e87n-display.service`。详见 [屏幕与风扇说明](display-fan.md)。

## 构建与输入

本次实际成功构建单元为 Lima VM `e87n-armbian` 中的 `e87n-display-fan-build.service`，不是旧 `e87n-armbian-build.service`：

- 开始：2026-09-13 09:05:25 CST；完成：09:15:45 CST。
- `SubState=exited`、`Result=success`、`ExecMainStatus=0`。
- Invocation ID：`b7c5b5bf08e540128605b5cae2b39dc1`。
- Armbian 框架：`7c1bb29eb0e7bd75b0703d86fe654b2680e646da`，保留已记录的主机 Python PATH 修补。
- Linux stable 固定提交：`f6388029ea9e2c9e807d73827658738ea131faee`，6.18.51；不是 7.2.5，也不跟随浮动分支。
- 本次内核制品标识：`6.18.51-Sf638-D0000-P77c8-C5fe3-H6edd-HK01ba-Vc222-Bc768-R448a`。
- 内核补丁共 13 个，新加入 `900-fb-nv3007-e87n.patch`；保留 vendor 作者及 GPL 声明。
- 冻结构建输入 `source-build-inputs.tar` SHA-256：`3e1eeb3d556d7f37090188c914e915e0f529b04e969b4bb4308292777a1995a5`。归档在编译前生成，最终交付说明在编译后另行更新。

这次构建使用独立开发工作区，原本地工作区及旧镜像保留。源码与公开文档的目标仓库为 [baozaodetudou/EDGEPI-E87N](https://github.com/baozaodetudou/EDGEPI-E87N)；GitHub 源码提交与本地二进制交付是不同事项，当前没有发布 GitHub Release 镜像附件。之后的文档及许可补充没有重编或改变本记录中的镜像。

## 产物及校验

完整 raw 镜像文件名：

```text
Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img
```

大小：`1170210816` 字节。SHA-256：

```text
679a35899f722157ecfe5da5cef5ed18c2c1cbbf13dbbe1bea41043c1827f543
```

**压缩、本机完整导出及最终校验已完成。** 原构建工作区的本地 release 目录为 `output/releases/2026-09-13-e87n-trixie-lts-6.18.51-display-fan/`，内含上述文件名的 `.img.xz`、`SHA256SUMS` 和 `RELEASE-NOTES.md`。

该目录被 Git 忽略，**不是 GitHub 下载链接**，克隆仓库不会得到镜像。公开获取状态及收到文件后的检查方法见 [镜像获取与校验](DOWNLOADS.md)。

压缩文件大小为 `181175072` 字节，SHA-256：

```text
25112263959e5eb133a959591bfc08fa39168853c8915577ead51efede2a3c8f
```

release 共 34 个文件，清单覆盖除清单自身外的 33 项，已在 VM 与 Mac 两端全数校验通过。Mac 的 `xz --test` 和 `xz -dc | shasum -a 256` 均通过；`xz --robot --list` 确认压缩/解压字节数为 `181175072` / `1170210816`，CRC64 正常。附带同一次镜像提取的 boot 组件、4 个匹配内核包、冻结输入及审计日志。`boot/` 是提取组件集，不是可以直接复制启动的原分区目录布局。

完整传输归档 `output/runtime/e87n-display-fan-release.tar` 的 SHA-256 为 `86aabbb45cc667f631ebad686518c466fb630cd2440fdafefcf3b8ca59d134e7`，两端一致。一次过早的本机预检拒绝了仍在 SCP 传输中的不完整归档；等待 SCP 正常退出后重新核验通过，未解包不匹配内容。

**不要把 Sept12 同名旧镜像当作新版。** VM 输出目录曾残留旧 `.img.xz`，已确认旧哈希后可恢复地移至本地备份目录，常规输出中的 `.img.xz` 现与本轮 release 一致。Mac 旧 release 没有修改，两次 raw 文件名虽然相同，但内容和校验值不同。

关键实际组件 SHA-256：

| 组件 | SHA-256 |
| --- | --- |
| ARM64 Image | `60772be8a42cb2a081df2e9dddb5080913496aeaf5cf159dbac95eb6275ef28b` |
| 最终内核 config | `d21a95a241c285b766bc23b7ceb704ed465ff8e170f64089d1f3570b43c4bbf4` |
| E87N DTB | `f6a89a7c774c0602ed22206efc91b02df8096a3a696e3479c6f9e92896d72356` |

真实根文件系统 UUID：`2ed94e68-5092-4dc0-8a32-0c596f3308df`，与该镜像 extlinux 和 fstab 一致；复制到测试介质后仍须避免多个设备出现相同 UUID。

## 验收证据与范围

1. 完整 ARM64 内核、模块、DTB、Debian 包和镜像构建成功。`fb_nv3007` 模块 vermagic 为 `6.18.51-current-filogic SMP mod_unload aarch64`，依赖 fbtft，含 NV3007 OF/SPI alias。
2. 对本轮 raw 执行 `verify-image.sh --release trixie --require-usb-root --require-display-fan` 通过。检查真实 GPT、ext4 `fsck -fn`、UUID、PHY 固件、最终配置与 DTB、NV3007 模块、默认 JSON、服务启用链接及 initramfs/USB-root 驱动链。只读挂载带 `noexec`，没有执行镜像程序。
3. 已安装的 4 个 Python 源文件、显示服务及默认 JSON 与冻结构建输入逐字节一致；目标 Debian/Pillow/字体包实际存在。镜像 Python 为 3.13.5，Pillow 包为 `11.1.0-5+deb13u4`，DejaVu 为 `2.37-8`。
4. `linux-image`、`linux-dtb`、BSP 和 base-files 在实际 dpkg 数据库中处于 hold；内核与 DTB 的 `Armbian-Original-Hash` 与本次制品标识一致，防止旧同名包混入。
5. 在独立 ARM64 Debian VM、Python 3.13.5 上，以 `umask 022` 运行 hardware 42、display 38、验证器 32 项测试，共 112 项通过。macOS Python 3.12.14/Pillow 12.3.0 也曾通过同组测试。预览图使用固定测试数据，不是屏幕照片或实机读数。
6. 主机 `systemd-analyze --root --generators=no --man=no verify` 对镜像内服务通过，未启动服务或执行目标生成器。这次额外检查使用单独的只读挂载，避免 `noexec` 干扰文件可执行位检查。
7. 构建启动器模拟回归、镜像验证器 64 项 mock 和 initramfs 验证器 40 项合成夹具回归通过。它们不触及真实 Docker、设备、initrd 执行或实机硬件，不能代替第 2 项真实镜像静态验收。

保留的非产品错误及复核：第一次可选 systemd 检查在 `noexec` 挂载上报 X_OK “Permission denied”；没有通过更改目标文件权限来掩盖它。一次普通 VM 用户的测试默认 `umask 002`，让夹具目录变成组可写，触发预期的严格配置安全检查；使用明确 `umask 022` 重跑全部通过，没有放宽生产校验。失败和复核日志均保留在 release 的 `evidence/`。

这些检查不是实机启动、散热或通用安全审计，也不是完整 DT schema 校验。CPU DVFS 仍禁用，MT7987 WED 仍不支持，固定 MAC 持久化、RAM fixup、原 U-Boot 加载方式、网口/eMMC/USB/NVMe/重启均有待上板验证。

## 首启及刷写边界

**不要把这张完整 GPT 镜像直接整盘刷入原 eMMC。** 跳过 U-Boot 构建/注入不等于保留原盘的 FIP、环境、factory 或 GPT；这也不是可通过 LuCI 升级的 OpenWrt sysupgrade 包。

先核实原 U-Boot 能力、分区和备份/恢复方式，再选择可恢复的临时加载与独立测试 rootfs。需要串口观察 NV3007 probe、屏幕方向/颜色、0/20/100%亮度及开关持久化，并确认风扇实际起转、温度与冷却档位变化后，才考虑受控负载测试。

本次没有改变原候选的默认账户/SSH 首启流程，也没有做新的生产安全加固。按已记录的默认 `root/1234`、串口自动登录和初始共享 SSH host key 风险处理：隔离首启、立即改密，核对密钥再生及唯一指纹之后再开放网络。详见 [首启验证与写入边界](first-boot.md)。
