# 镜像获取、发布状态与校验

本仓库公开的是构建源码、板级补丁、Debian 小屏程序、测试与文档。
**当前没有上传 GitHub Release 镜像附件，也没有自动构建下载链接。**
`git clone` 不会下载之前本地生成的 `.img.xz`、内核包、完整日志或构建缓存。
需要自行按照 [构建指南](BUILDING.md) 生成，或者取得维护者明确提供的候选文件后校验。

## 已生成的最新本地候选

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

在存放候选文件的目录执行；以下只读检查不刷盘：

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
它不是上面记录的新版。

## 未来公开镜像时

应使用单独的版本化 Release 附件，而不是强行将大镜像、`.deb` 或私有日志塞入 Git。
发布前必须重新核验附件、提供对应源代码/构建输入与许可证、清除共享 SSH 密钥等首启安全问题，
并在标题和说明中显著标注实验性及实机验收状态。此文不代表这些发布步骤已经完成。

## 禁止当作直接刷机指南

完整 `.img` 包含 GPT，不是 OpenWrt sysupgrade/FIT。不要在 LuCI 上传，
也不要在没有核对原 U-Boot、分区、备份和恢复方式时整盘写入 eMMC。
先阅读 [首启与写入边界](first-boot.md)。
