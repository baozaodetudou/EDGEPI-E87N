# E87N 只读诊断

当前最小镜像通过独立版本化 `e87n-display` 包预装 `e87nctl doctor`，包说明见
[DISPLAY-PACKAGE.md](DISPLAY-PACKAGE.md)。登录与系统默认值以 [DEFAULTS.md](DEFAULTS.md)
为准：`root` / `doumao`、SSH 22、networkd/netplan DHCP、上海时区、中文 UTF-8 与正常 APT。
在成功启动的新 Debian 系统登录、改密后运行：

```sh
e87nctl doctor
```

该命令不要求创建管理员或先完成公钥初始化。额外 DM/RAID 等存储模块默认
`E87N_EXTRA_STORAGE=no`，管理工具按需安装；诊断不把它们当作默认预装能力。

`board-support/e87n/doctor.py` 提供 `doctor(root="/")`，只使用 Python 标准库，
返回可 JSON 序列化的字典。目标为 Debian 13 Trixie、Linux 6.18.51、EdgePi E87N。
它观察当前内核和调用者所在挂载/网络环境公开的数据；容器、chroot 或未挂载
proc/sys 的离线目录会使结果缺失或只反映该环境，不能替代整盘镜像验证。

## Python 与 CLI 接入契约

主程序已接入 `e87nctl doctor`，使用下面的契约；诊断分支不会初始化硬件写接口。

```python
import json
from e87n.doctor import doctor

result = doctor()
print(json.dumps(result, sort_keys=True, allow_nan=False))
# CLI 分支返回 result["exit_code"]。
```

返回字段：`schema_version=1`、`status`、`exit_code`、`checks`、`warnings`、
`errors`、`hardware_validation`。`checks` 是固定名称的字典，每项包含
`status`、静态 `summary` 和 `details`；缺失、不可读、格式错误或超限的数据使用
`null` 或明确的空集合/不完整标志，不用默认硬件数值代替。

| 检查状态 | 含义 |
| --- | --- |
| `ok` | 本项限定的观测条件满足 |
| `warning` | 观测到需要关注的情况，或可用性/枚举不完整 |
| `unknown` | 数据缺失、不可读、无效或有歧义，无法判断 |
| `error` | 观察到目标身份不符，或注入根目录不可用 |

任一 `error` 使总体 `status=error`、`exit_code=2`；否则，任一 `warning` 或
`unknown` 使总体 `status=warning`、`exit_code=1`；全部 `ok` 才是退出码 0。
`warnings` 包含 warning/unknown 检查项的名称，`errors` 包含 error 项名称。
调用者应查看对应 `checks` 的说明和数据。

**所有结果始终包含 `hardware_validation: "not-performed"`，包括退出码 0。**
它也不是首启安全、SSH 密钥唯一性、登录策略或生产就绪认证。

## 固定诊断项

| 名称 | 证据与判断边界 |
| --- | --- |
| `root_access` | 选定根必须为可访问的绝对目录。默认 `/`；仅 Python API 支持夹具注入。 |
| `os` | `/etc/os-release`，不可读取时尝试 `/usr/lib/os-release`。只解析 `ID`、`VERSION_ID`、`VERSION_CODENAME`，不执行文件。Debian 13/13.x 满足版本检查；缺少代号以 null 报告，明确非 Trixie 会报错。 |
| `kernel` | `/proc/sys/kernel/osrelease`。比较严格的三段基础版本 `6.18.51`；允许发行版后缀但不输出后缀，`6.18.510` 不匹配。不以主机 `uname` 补足缺失值。 |
| `device_tree` | `/sys/firmware/devicetree/base/{model,compatible}` 的 NUL 结尾属性，检查 `EdgePi E87N` 与完整兼容串 `edgepi,e87n`，不输出任意设备树文本。 |
| `memory` | `/proc/meminfo` 唯一、有效的 `MemTotal`，单位 KiB；≤262144 KiB（256 MiB）发出警告，建议核对 DTS/U-Boot 内存交接。1024 MiB 只是已知板卡的标称比较参考，不用它填充实际检测值，也不保证高于 256 MiB 就识别了全部 RAM。 |
| `root_filesystem` | `/proc/self/mountinfo`，不可读取时尝试 `/proc/mounts`。仅报告 `/` 是否存在、文件系统类别与 ro/rw；挂载点或 superblock 任一为 ro 都报告只读。多个根挂载有歧义，临时/只读根发出警告。不输出来源设备、UUID 或挂载选项原文。 |
| `cmdline` | `/proc/cmdline` 的非空状态、非空 `root=`、重复 root 参数、`rootwait`、`mem=` 和 ro/rw 冲突。仅报告存在性，绝不输出参数值。缺少 root 参数或出现内存限制等情况发出警告。 |
| `network` | 有界枚举 `/sys/class/net` 的非 lo 接口，读取 `carrier`、`operstate`、`addr_assign_type`。至少一个 carrier=1 且来源数据有效、没有随机地址时满足检查；其他未连接端口本身不导致警告。carrier 不证明 DHCP、DNS、互联网或各物理网口均可用。 |
| `thermal` | 有界枚举 thermal zones，报告 CPU/SoC 分类及有效毫摄氏度。需要至少一个可读取的 CPU/SoC 温度；不评估精度、额定安全温度或负载散热。 |
| `fan` | 只认 `type=pwm-fan` 的 cooling device，以及 `name=pwmfan/pwm-fan` 的 hwmon。报告当前/最大档位、PWM 和可选 RPM；缺失测速线时 RPM 为 null，不把档位或百分比换算成转速。可读取控制档位不证明风扇实际起转。 |
| `nv3007` | 只认 `/sys/class/graphics/fb*/name=fb_nv3007`，检查可见的 428×142、16 bpp 几何信息；并列报告模块加载与配置证据。仅有模块、配置或 DT 节点不会使此项通过。没有打开 framebuffer、读取像素或点亮背光。 |
| `kernel_config` | 依次尝试 `/proc/config.gz`、`/proc/config`、与有效运行版本精确对应的 `/boot/config-<release>`。取第一个可读取来源；损坏的已读配置不退回别的副本。boot 文件名匹配不证明运行内核内容相同，因此使用 boot 副本时发出警告。 |
| `kernel_modules` | `/proc/modules` 中固定白名单模块的加载状态。不返回内核地址、引用者或完整模块清单。未出现在列表中可能是内建或尚未加载，不能据此断言驱动缺失。 |
| `containers` | 配置中的 cgroups、memory/pids controller、namespaces、seccomp、veth、bridge/netfilter、nftables/NAT、overlayfs 等基础前提。没有运行容器、创建 namespace、检查 cgroup 挂载或测试防火墙。 |
| `storage` | 配置中的 ext4、MMC/MMC_BLOCK/MMC_MTK、NVMe、SCSI/BLK_DEV_SD、USB_STORAGE/USB_UAS。没有探测磁盘、检查介质健康、挂载文件系统或进行 I/O 测试。 |

网络接口使用本次排序后的 `interface` 序号，不返回接口名，因为 `enx...` 名称
本身可能包含完整 MAC。该序号不是跨启动的物理端口标识。`addr_assign_type`
采用内核值：0=`permanent`、1=`random`、2=`inherited`、3=`set`；1 会警告，
2/3 只说明来源方式，不自动推断为安全问题。缺失/越界值为 null。

配置状态分别保留 `y`（内建）、`m`（模块）、`n`（显式禁用）、null（缺失、
格式不符或重复赋值）。模块模式不证明对应 `.ko` 已安装或能加载；doctor 不扫描
模块包，也不运行 `modprobe`。这些检查不能替代镜像验证器对真实模块链的审计。

已知约 1 GB RAM 的比较依据来自[原系统只读记录](openwrt-hardware-reference-20260913.md)：
原 OpenWrt 的 `MemTotal=1011132 kB`。该历史证据不代表当前 Debian 识别值。

## 只读、隐私与路径限制

导入没有诊断 I/O；调用不会写配置、改变 PWM/网络、加载模块、启动服务、调用
shell/subprocess、访问网络或打开 `/dev`。它只读取选定 proc/sys 属性和发行版/
内核配置文件。普通磁盘文件的读取仍可能由文件系统更新 atime；这里“只读”表示
程序不发起写入、控制 ioctl 或配置修改，不承诺文件系统元数据绝不变化。
运行测试或独立 Python 调用时使用 `-B` 可禁用解释器字节码缓存写入。

不采集/输出 MAC、IP、序列号、machine-id、密钥、密码、任意 os-release 文本、
完整 cmdline、模块地址或来源磁盘标识。摘要与错误信息为固定文本，不包含原始
异常消息、注入目录或解析后的 sysfs 设备路径。cmdline、mountinfo、modules
可能在读取时含敏感字段，但只输出白名单判断；不要把这些源文件另行整体附到报告。

每个小属性最多 16 KiB，配置、模块表、挂载表最多 1 MiB；gzip 的压缩输入和
解压输出各有 1 MiB 上限，超限不接受前缀。每个目录最多查看 256 个条目、选择
64 个匹配项，不递归；到达限制会报告枚举不完整。路径最多 64 个组成部分，
每次读取最多跟随 16 个符号链接，链接文本最多 4096 字符。

注入根首先规范化为调用者指定的目录，后续逐级以目录描述符和 `O_NOFOLLOW`
打开。普通 sysfs class→devices 链接和 os-release 链接可用；相对链接不能越过
根，proc/sys 链接也不能跨出各自树。绝对链接以注入根为 `/` 解释，不会跳回
开发主机的 `/sys`、`/proc` 或 `/usr`。直接指向规范化夹具根内的绝对链接也可用。
os-release 链接只允许两个已选定发行版
文件之间的解析。只接受目录与普通属性文件，拒绝 FIFO、设备类型与循环链接。
这些限制不是对恶意内核、特权者替换挂载点或并发重命名整个目录树的隔离保证。

## 假文件系统测试与实机验收

```sh
python3 -B tests/test-doctor.py
```

测试只创建临时普通文件、符号链接和拒绝场景的 FIFO，所有诊断打开操作均由
测试护栏约束到夹具根，禁止写入与进程启动 API。覆盖身份/内存边界、缺失和
损坏数据、配置来源、模块与设备注册区别、隐私过滤、压缩上限、目录枚举上限、
越界/循环/绝对链接和检查后替换链接等情况。不运行真实主机或设备诊断。

功能验收必须另行在受控 Debian 实机上执行并记录：启动与 RAM 交接、每个物理
网口的地址获取和收发、存储读写与重启、按需安装后的容器工作负载、实际屏幕颜色/方向/亮度、
风扇起转及负载温升。doctor 不执行这些步骤；其 JSON 永远不会将这类测试标成通过。

当前 VM 副本上的 APT、locale、SSH/PAM、密钥与静态系统检查结果见
[TESTING.md](TESTING.md)，不能替代实体启动。CPU DVFS/CPU cooling 仍禁用；
DTS 默认 256 MiB 与原系统记录的 1 GiB RAM 仍需核对 U-Boot fixup，缺少测速反馈时 RPM 不可用。
