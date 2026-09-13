# 镜像获取、发布状态与校验

本仓库公开的是构建源码、板级补丁、Debian 小屏程序、测试与文档。
**当前没有可报告的新最小配置成功构建 run 或 GitHub Release 镜像附件。**
`git clone` 不会下载之前本地生成的 `.img.xz`、内核包、完整日志或构建缓存。
可按 [构建指南](BUILDING.md) 自行生成，或在 [GitHub Actions](GITHUB-ACTIONS.md) 对应 run 成功并上传 artifacts 后取得文件，再校验。工作流文件存在、任务开始运行与成功产物上传是不同状态；artifacts 也不等同于 Release。

## 当前最小配置的交付状态

新配置以 [DEFAULTS.md](DEFAULTS.md) 为准：`root` / `doumao`、SSH 22、DHCP、上海时区、中文 UTF-8、正常 APT 和预装版本化 `e87n-display` 包；额外存储默认 `E87N_EXTRA_STORAGE=no`。新完整镜像及其 SHA-256 尚待记录。

已通过的 VM 用户空间集成只转换可丢弃的旧候选 rootfs 副本，见 [TESTING.md](TESTING.md)，不能把副本或原镜像作为新配置交付。2026-09-13 11:10:49 CST 完成的旧配置 VM 构建也已被替代，不代表新最小配置构建完成。

只有成功 run 中与目标提交、配置、日志及校验清单一致的 artifacts 才能作为该次构建产物下载。显示包的独立版本与升级方法见 [DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)；云端运行与获取方式见 [GITHUB-ACTIONS.md](GITHUB-ACTIONS.md)。目前没有据此宣称新的已发布镜像。

## 历史本地屏幕/风扇候选（旧配置）

以下文件、大小和哈希仅保留历史含义，不对应当前最小配置。

2026-09-13 屏幕/风扇版：Debian 13.6 Trixie + Linux 6.18.51-current-filogic。
完整构建、真实镜像只读审计、Mac 导出与文件校验已完成，但尚未上板测试。
这不是在 GitHub 发布了二进制镜像的声明。

```text
文件名：Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
压缩大小：181175072 字节（约 173 MiB）
压缩 SHA-256：25112263959e5eb133a959591bfc08fa39168853c8915577ead51efede2a3c8f
解压大小：1170210816 字节
解压 SHA-256：679a35899f722157ecfe5da5cef5ed18c2c1cbbf13dbbe1bea41043c1827f543
```

原构建机本地交付目录为 `output/releases/2026-09-13-e87n-trixie-lts-6.18.51-display-fan/`，
由 `.gitignore` 排除，不是可在 GitHub 浏览的目录。详细证据见 [候选记录](candidate-display-fan-20260913.md)。

## 收到文件后检查

在存放候选文件的目录执行；以下沿用历史文件名示范，只读检查不刷盘。新产物可能同名，必须与其自己的 run、配置及校验清单比对，不能使用上面的历史哈希验收新配置：

```sh
xz --test Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
shasum -a 256 Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz
xz -dc Armbian-unofficial_26.11.0-trunk_Edgepi-e87n_trixie_current_6.18.51_minimal.img.xz | shasum -a 256
```

Linux 可用 `sha256sum` 替代 `shasum -a 256`。若同时收到完整交付目录及 `SHA256SUMS`，
在该目录使用 `shasum -a 256 -c SHA256SUMS`；清单应与实际交付文件集合匹配。
哈希一致验证文件内容，不代表可信签名、硬件功能正常或刷写安全。

文件名和内核版本相同不代表同一个构建：2026-09-12 的旧 Trixie 文件缺少新增屏幕支持，
压缩 SHA-256 是 `32474999d280d4a9057985c6ba6985b1ed5f223584b7f8fe1809f9e4f48cb33e`。
它也不是上面记录的 9 月 13 日历史屏幕/风扇候选。

## 未来公开镜像时

应使用单独的版本化 Release 附件，而不是强行将大镜像、`.deb` 或私有日志塞入 Git。
发布前必须重新核验附件、提供对应源代码/构建输入与许可证，并核对当前默认密码说明及独立 SSH 密钥首启配置，
并在标题和说明中显著标注实验性及实机验收状态。此文不代表这些发布步骤已经完成。

## 禁止当作直接刷机指南

完整 `.img` 包含 GPT，不是 OpenWrt sysupgrade/FIT。不要在 LuCI 上传，
也不要在没有核对原 U-Boot、分区、备份和恢复方式时整盘写入 eMMC。
先阅读 [首启与写入边界](first-boot.md)。
