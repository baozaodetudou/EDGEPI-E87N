# E87N RAM-only FIT 诊断流程

本文只用于判断 E87N 的 U-Boot/FIT/内核/内存交接问题。它不会刷写 eMMC，也不会把诊断 FIT 当作生产固件。源码中的生成入口是：

```text
scripts/ramdiag/build-fits.py
scripts/ramdiag/build-initrd.py
scripts/ramdiag/verify-fit.py
scripts/ramdiag/extract-fit-payload.py
scripts/ramdiag/receive-udp.py
```

## 三个实验文件

一次构建默认生成三个文件：

| 文件 | 目的 | 预期证据 |
| --- | --- | --- |
| `E87N-ramdiag-40000000-initrd.itb` | 基线：内核加载到 `0x40000000`，带 RAM-only initrd | 若 initrd 的 `/init` 正常，应能通过串口和 UDP 看到 Linux/userspace；历史诊断 initrd 还可以提供 SSH |
| `E87N-ramdiag-40080000-initrd.itb` | 只改变内核 load/entry 地址的 A/B 实验 | 用来判断厂商 U-Boot 是否正确处理 `0x40080000` 与 ARM64 `text_offset=0` 的关系；不能预先把它当作正确地址 |
| `E87N-ramdiag-40000000-no-initrd.itb` | 不带 initrd，只验证 FIT 解包、内核早期启动和串口 | 不应有 DHCP/SSH；停在 VFS/root/init 错误是预期现象，重点是不能自动重启并能留下串口日志 |

三种 FIT 使用同一个内核和 DTB。脚本会复制 DTB 到临时目录后强制设置：

```text
/soc/mmc@11230000/status = "disabled"
/chosen/bootargs       = ... panic=0 ...
```

因此诊断 FIT 不应出现 `/dev/mmcblk0`，也不会把原 eMMC 当作根文件系统。内核、ramdisk、FDT 都使用 FIT 内嵌数据和 SHA-256 hash；不会使用外部 FIT data-offset。

## 从当前已存在的历史 FIT 重建

每次成功的 Firmware image job 还会在同一个候选 artifact 的 `diagnostics/` 目录放入一套新的诊断 FIT。它们不会进入 Firmware Release；该 Release 只公开 `*-uboot-firmware.tar`。诊断 FIT 也不能写入 eMMC，且与独立 Display Release 无关。若要本地重建，先从镜像的 boot/root 分区生成网络诊断 initrd，再运行 FIT 生成器；CI 使用的完整入口是：

```sh
sudo bash scripts/ci-build-ramdiag.sh \
  --image /path/to/candidate.img \
  --output-dir output/ramdiag-run-$(date +%Y%m%d)
```

如果手上只有历史临时产物，例如 `output/ramdiag/sshfix-artifacts.RuHfhl/E87N-Armbian-6.18.51-ramdiag.itb`，也可以只读提取其三个 payload：

```sh
python3 scripts/ramdiag/extract-fit-payload.py \
  output/ramdiag/sshfix-artifacts.RuHfhl/E87N-Armbian-6.18.51-ramdiag.itb \
  --output-dir /tmp/e87n-ramdiag-input
```

也可以直接使用同一套经过审核的 `Image.lzma`、E87N 1 GiB DTB 和 RAM-only initrd。这里的 initrd 必须是普通 initrd（通常为 gzip 压缩 cpio），不能再套一层 legacy `uInitrd` 头。生产 Debian initrd 会寻找 eMMC 根 UUID，不能直接当作 RAM-only initrd 使用。

在 Linux 主机或安装了 `u-boot-tools` 的构建环境执行（生产 initrd 仅用于 FIT 结构实验；优先使用上面的 CI 入口生成 network-first initrd）：

```sh
python3 scripts/ramdiag/build-fits.py \
  --kernel /tmp/e87n-ramdiag-input/kernel.payload \
  --dtb /tmp/e87n-ramdiag-input/board.dtb \
  --initrd /tmp/e87n-ramdiag-input/ramdisk.payload \
  --output-dir output/ramdiag-run-20260920
```


`--kernel-compression=auto`（默认）使用生产侧相同的 LZMA-Alone 参数（8 MiB dictionary、`lc=1 lp=2 pb=2`）。实机保存的 E87N 原厂 U-Boot FIP 中包含 `lzma`、`lzmadec` 和 FIT compression 支持，因此默认诊断 FIT 与生产 FIT 保持一致；如果要做未压缩 A/B 实验，可显式传 `--kernel-compression=none`。FIT 文件还必须严格小于 `0x06000000`（96 MiB）；这是 E87N U-Boot `CONFIG_SYS_BOOTM_LEN` 和 RAM loader 的硬上限，生成器、验证器和提取器统一按 `0x05ffffff` 拒绝更大的 FIT。输出目录中会有每个 FIT 的 JSON 报告和一个 `MANIFEST.json`。脚本默认拒绝覆盖已有文件；若输入只有无 initrd 变体，可运行：

`verify-fit.py` 默认要求 `compression = "lzma"`；只有明确要审计未压缩实验 FIT 时，才传 `--kernel-compression=none`。`--kernel-compression=auto` 仅用于读取 FIT 元数据。

```sh
python3 scripts/ramdiag/build-fits.py \
  --kernel /path/to/Image.lzma \
  --dtb /path/to/mt7987a-edgepi-e87n.dtb \
  --variant 40000000-no-initrd \
  --output-dir output/ramdiag-no-initrd-20260920
```

单独复核已生成的文件：

```sh
python3 scripts/ramdiag/verify-fit.py \
  output/ramdiag-run-20260920/E87N-ramdiag-40000000-initrd.itb \
  --variant 40000000-initrd \
  --kernel-sha256 "$(jq -r '.variants[] | select(.variant=="40000000-initrd") | .kernel_payload_sha256' output/ramdiag-run-20260920/MANIFEST.json)" \
  --initrd-sha256 "$(jq -r '.variants[] | select(.variant=="40000000-initrd") | .ramdisk_sha256' output/ramdiag-run-20260920/MANIFEST.json)"
```

构建前后应保存：FIT 文件 SHA-256、manifest、`dumpimage -l <file>`、内核源文件 SHA-256、DTB 源文件 SHA-256、initrd 源文件 SHA-256。静态通过只说明文件结构正确，不说明板上一定启动。

## U-Boot 前的主机准备

优先使用独立网线直连，不要让设备同时接回普通路由器。假定 U-Boot 的固定地址仍是此前记录的 `192.168.1.1`，主机直连接口是 `en7`；如实际接口不同，只替换接口名。

macOS：

```sh
sudo ifconfig en7 inet 192.168.1.2 netmask 255.255.255.0 up
```

### 网络硬门禁

下面的门禁必须全部满足后，才允许上传或启动任何 RAMdiag FIT。`ifconfig`、`route`、`arp`、`ping` 和 `curl` 只检查主机与网络状态；它们不会写 E87N 闪存。

```sh
ifconfig en7
route -n get 192.168.1.1
arp -an | grep '192.168.1.1' || true
ping -c 2 -W 1000 192.168.1.1 || true
curl --connect-timeout 2 --max-time 4 -sS -D - -o /dev/null http://192.168.1.1/
```

至少要看到：

1. 直连接口为 `status: active`，且主机确实有 `192.168.1.2/24`；
2. 到 `192.168.1.1` 的路由走直连接口；
3. ARP 不是 `(incomplete)`；
4. HTTP 页面能够证明自己是 E87N U-Boot 恢复页，而不是 OpenWrt LuCI。

U-Boot 未必响应 ICMP，所以单独 `ping` 失败不能判定 U-Boot 不在；但如果接口是 inactive、主机没有 `192.168.1.2`、ARP 仍为 incomplete，或网页完全不可达，就必须把控制通道视为**未建立**。此时停止，不上传 FIT、不点击临时启动、不点击升级，也不要用网页显示的 `success` 推断 Linux 已启动。若观察到 `en7: status: inactive` 或没有 `192.168.1.2`，就是未通过该门禁。

Linux：

```sh
sudo ip addr replace 192.168.1.2/24 dev <直连接口>
sudo ip link set <直连接口> up
```

先启动网络证据接收器，再上传/启动 FIT：

```sh
python3 scripts/ramdiag/receive-udp.py \
  --bind 192.168.1.2 --source 192.168.1.1 \
  --output output/ramdiag-run-20260920/udp-40000000.log \
  --seconds 600
```

这个接收器只接受 `192.168.1.1` 发来的 UDP，不发送任何控制数据。历史 initrd 的 beacon 使用 UDP `6666`；如果本次 initrd 没有 beacon，UDP 没数据不代表内核没启动，必须看串口。

串口应在上电/点击启动前就打开，参数固定为 115200 baud、8 数据位、无校验、1 停止位、无硬件流控。macOS 可以用 `screen` 保留会话记录：

```sh
script -q output/ramdiag-run-20260920/serial-40000000.log \
  screen /dev/cu.<实际串口设备> 115200
```

也可以使用 `picocom`/`minicom`，但必须把完整串口输出保存到与 FIT SHA-256 同一份证据目录。

## U-Boot 中先做只读检查

进入 U-Boot 后，不要执行 `saveenv`，先记录：

```text
version
bdinfo
printenv ipaddr serverip netmask gatewayip loadaddr kernel_addr_r ramdisk_addr_r fdt_addr_r
printenv fdt_high initrd_high bootm_low bootm_size bootm_mapsize bootcmd boot_targets
```

重点确认：

1. `ipaddr`/`serverip` 是否仍为 `192.168.1.1`/`192.168.1.2`；
2. `loadaddr`、`bootm_low`、`bootm_size` 是否与 FIT 源文件上传区和 `0x40000000`–`0x46000000` 的目标区冲突；
3. U-Boot 是否明确支持 `bootm` 和 FIT，而不是只支持厂商 `mtkboardboot` 私有路径；
4. 是否有 watchdog/自动重启设置。不要在没有串口记录时凭网页显示的 `success` 认定 Linux 启动成功。

## 两种加载方式

### 方式 A：已有 U-Boot Web 页面

这是当前设备已经用过的方式，也是优先方式：

1. 确认页面确实是 U-Boot 恢复页，不是正在运行的 OpenWrt LuCI。
2. 只把一个诊断 `.itb` 上传到页面的 `initramfs` 字段。
3. 不要把它填入 `kernel`/`firmware` 字段，不要点击升级、写入、sysupgrade 或任何会写 p4/p5 的动作。
4. 对照本机计算的大小和 SHA-256；页面如果只回显 MD5，也同时计算 `md5 <file>` 比对。
5. 通过页面的临时 boot/run 动作启动，启动前已经打开串口和 UDP 接收器。
6. 每次只测试一个变体；测试结束回到 U-Boot 再换下一个，不要在一个页面会话里连续上传多个文件。

历史记录中的 Web `success` 只代表 U-Boot 接受了上传/boot action，不代表 Linux 已进入 userspace；真正的通过条件是串口或 UDP 中出现对应阶段证据。

### 方式 B：U-Boot 控制台 + TFTP

如果 U-Boot 有 TFTP 客户端，在主机用任意可信 TFTP server 发布输出目录，然后在 U-Boot 中执行（不保存环境）：

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.2
setenv netmask 255.255.255.0
setenv loadaddr 0x48000000
ping 192.168.1.2
tftpboot ${loadaddr} E87N-ramdiag-40000000-initrd.itb
iminfo ${loadaddr}
bootm ${loadaddr}
```

`0x48000000` 只是此前保存环境中的上传地址示例，最终以本次 `bdinfo`/`printenv` 为准。如果 `ping` 不通、TFTP 失败、`iminfo` 报 FIT 错误或 U-Boot 报内存重叠，停止，不要尝试 `saveenv` 或刷生产包。

## 绝对禁止的命令和入口

在本次 RAM 诊断阶段不要执行以下会改变持久化环境、eMMC、SPI-NAND/NOR 或整盘布局的操作：

```text
saveenv
env save
fw_setenv
mmc write
mmc erase
sf write
sf erase
nand write
nand erase
gpt write
mtkupgradefw
fastboot flash
sysupgrade
```

Linux 侧同样禁止把任何输出重定向到设备节点，或改变分区/文件系统：

```text
dd of=/dev/mmc*
mkfs /dev/mmc*
blkdiscard /dev/mmc*
parted 修改 eMMC
sgdisk --zap-all
resize2fs /dev/mmcblk0p5
```

`setenv` 不会立即写闪存，但会改变当前 U-Boot 会话；在网络硬门禁通过并且已记录原值前不要使用，任何情况下都不要配合 `saveenv`。`bootm` 只允许用于已经通过主机 SHA-256/结构审计、且明确装入 RAM 临时地址的 RAMdiag FIT；生产 TAR、SIMG、GPT、FIP 或 LuCI sysupgrade 都不是 RAMdiag 输入。诊断过程中也不要反复执行 `reset`/`reboot`，否则会丢失看门狗、地址冲突和内核崩溃的时序证据。

## 三个变体的测试顺序和判定

每个变体都记录同一份表：U-Boot `version`、环境摘要、FIT SHA-256、上传字节数、启动时间、串口日志、UDP 日志、链路状态、是否出现 SSH、是否重启。

### 1. `40000000-initrd`

这是唯一首先测试的网络诊断变体。成功证据应按顺序接近：

```text
Starting kernel ...
Linux version 6.18.51...
eth0 or eth1 / PHY link up (the linked E87N MAC is selected at runtime)
RAM diagnostic init entered
eMMC disabled/absent
RAMDIAG READY
```

如果使用的是历史已测试的 SSH initrd，再尝试：

```sh
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no root@192.168.1.1
```

但 SSH 只有在该 initrd 的 `/init` 确实包含临时 host key、监听配置、`devpts`/PTY 和可执行的 `sshd` 服务时才是有效检查项。当前服务脚本从 `PATH` 解析 `sshd`，并用 `wait` 回收退出的 sshd，避免非 merged-/usr 布局或僵尸子进程造成误判。过去的失败正是裸调用 `sshd -D` 使 PID 1 退出；不能只看内核启动线就认为 SSH initrd 合格。

### 2. `40080000-initrd`

在同一 U-Boot 会话中不要复用旧文件名；重新上传/加载第二个 FIT。比较两份串口：

- `40000000` 能到 userspace、`40080000` 在 `Starting kernel` 后立即停/重启：高度怀疑厂商 FIT/bootm 路径没有为 `text_offset=0` 做正确搬移；
- 两者都能到 userspace：load address 不是当前主要故障；继续查 initrd、驱动、watchdog 或生产 rootfs；
- 两者都在 Linux 早期同一点停：更像共享的 DTB、内核、内存保留区或 initrd 问题。

### 3. `40000000-no-initrd`

这是串口实验，不要等待 DHCP/SSH。理想证据是能看到完整的内核早期输出，随后停在类似 VFS/root/init 的错误，而且因为 `panic=0` 不自动重启。若它也在 `Starting kernel` 前后直接复位，则应优先怀疑 U-Boot 地址/内存交接、看门狗、电源或硬件，而不是 Debian userspace。

## 证据收集和故障定位

建议每个变体使用单独目录，例如：

```text
output/ramdiag-run-20260920/
  E87N-ramdiag-40000000-initrd.itb
  E87N-ramdiag-40000000-initrd.itb.json
  serial-40000000.log
  udp-40000000.log
  uboot-40000000.txt
```

`uboot-*.txt` 至少包含 `version`、`bdinfo`、上述 `printenv`、`ping`/TFTP/上传结果和 U-Boot `bootm` 输出。不要把完整 U-Boot 环境、MAC、序列号、密码或恢复备份公开到 Git；本地证据可按需脱敏。

如果串口显示 Linux Oops/panic，先看是否真的是 U-Boot 复位。当前诊断
initrd 会在 `devtmpfs`/`/run` 刚准备好时就启动 watchdog keeper，并在设备节点
出现竞态或 ioctl 暂时失败时自动重试；同时保持独立的 shell supervisor 作为 PID 1；
这样 `sshd` 或其他诊断服务退出时不会把 PID 1 杀掉而触发内核的
`Attempted to kill init` panic：

- `panic=0` 下 Linux 应停住；仍然周期性重新出现 U-Boot banner，说明是 watchdog/电源/bootloader 复位或内核在其他路径主动复位；
- 如果只看到 `RAMDIAG WATCHDOG device did not become available`，先保存
  `/run/ramdiag/watchdog.log` 和串口；不要继续上传生产固件，因为这表示诊断环境
  没有拿到可确认的 watchdog 控制权；
- 只出现一次 `Starting kernel` 后无任何输出，比较两个 kernel load 变体，并检查 `bootm_low/bootm_size/initrd_high/fdt_high`；
- no-initrd 能跑到 VFS 错误、initrd 变体不能，说明 kernel/FDT 早期路径基本成立，问题集中到 FIT ramdisk 装载、initrd 内容或 `/init`；
- RAM-only initrd 能 SSH、生产固件仍重启，说明不能把诊断结果外推为生产 rootfs/首启/驱动完整通过。

在三种变体都完成串口证据前，不再上传生产 TAR，不执行 Web upgrade，不写 p4/p5，也不执行 `saveenv`。
