# e87n-display Debian 软件包

`e87n-display` 是供 E87N minimal Debian 13（Trixie）镜像预装、也可单独升级的
显示与诊断包。版本独立于镜像、Armbian 和内核；默认版本来源为
`packaging/e87n-display/VERSION`。镜像启动器将打包脚本、`packaging/` 与
`board-support/` 按仓库相对结构复制到 `/tmp/overlay/e87n-package/`，customize 在
目标 chroot 中调用该脚本，再通过 APT 安装产物。当前源码已经接入预装流程，
并启用 LCD/背光节点及显示服务；包版本以 `packaging/e87n-display/VERSION` 为准，当前为
`1.3.1-1`。
独立 deb 用于升级和重新安装，不表示基础镜像默认无屏幕。
candidate3 属于此前的 headless 配置，其验收不能替代当前显示配置的重建与实机测试。

显示包通过独立的 Display workflow / Release 发布。每个 Display Release 只公开一个
`e87n-display_<version>_all.deb`，不包含 firmware TAR。固件构建仍会从同一份源码生成一个
基线 deb、预装到 rootfs 并交给 QEMU 验证；这只绑定固件内的预装基线，不要求后续 Display
Release 与 Firmware Release 使用相同 tag、run、版本或 SHA-256。兼容的显示更新可以独立
高频发布，无需重建固件。

正式 Display tag 固定为 `e87n-display-v<version>`，不包含 Actions run ID。同一 Debian
版本只能发布一次；源码或包内容变化时必须先递增 `packaging/e87n-display/VERSION`，否则
preflight 会因 tag/Release 已存在而拒绝发布。run/attempt 仅用于 Actions 候选 artifact 和
构建证据绑定。

### NV3007 framebuffer 兼容性

Linux fbtft 的 `fb_nv3007` 会在 `fb_var_screeninfo.nonstd` 中返回
`FB_NONSTD_HAM`（值为 `1`）。这是 fbtft 的兼容性标记；在 E87N 上实际像素仍是
标准 16-bit RGB565（R11/G5/B0），并不表示需要按 HAM 格式解码。显示程序只接受
该已知标记，仍严格校验 framebuffer 身份、428×142 几何、truecolor、RGB565 位域、
stride、偏移和 framebuffer 内存边界。

## 构建接口

在 Debian/Linux 主机上使用 Bash、`dpkg`、`dpkg-deb` 和 GNU coreutils/findutils，
不需要 root、debhelper、目标板硬件、网络或主机上的 Pillow：

```sh
./scripts/build-display-deb.sh --output-dir ./output/debs
./scripts/build-display-deb.sh --output-dir ./output/debs --version 1.3.1-1
```

产物严格命名为 `e87n-display_VERSION_all.deb`，例如
`output/debs/e87n-display_1.3.1-1_all.deb`。标准输出包含构建进度，调用方应按版本
构造文件名，不能将整个 stdout 当作路径。版本须为合法 Debian 版本，发布新内容时
递增版本；可用 `dpkg --compare-versions` 检查先后。构建先写独立临时目录，成功后
替换同名产物；失败不覆盖原产物。`dpkg-deb --root-owner-group` 固定包内 root:root
所有权；构建脚本默认 `SOURCE_DATE_EPOCH=0`。Firmware workflow 固定该值，用于确认预装
基线包与 QEMU 实际测试包来自同一构建输入；Display workflow 对自己发布的 deb 独立记录
SHA-256。两个通道的 deb 不要求逐字节相同。macOS 缺少 dpkg 时会明确报错，可在已有 Linux
VM 中构建。

## 包内容与边界

| 来源 | 安装位置 |
| --- | --- |
| `board-support/e87n/` 的显式五文件白名单（见下文） | `/usr/lib/python3/dist-packages/e87n/` |
| `board-support/e87nctl` | `/usr/bin/e87nctl` |
| `board-support/display.json` | `/etc/e87n/display.json`（conffile） |
| `board-support/e87n-display.conf` | `/etc/modules-load.d/e87n-display.conf`（conffile） |
| `board-support/systemd/e87n-display.service` | `/usr/lib/systemd/system/e87n-display.service` |
| `packaging/e87n-display/copyright` 与 `README.Debian` | `/usr/share/doc/e87n-display/` |

Python 白名单为 `__init__.py`、`__main__.py`、`hardware.py`、`display.py`、`doctor.py`；
缺少任何一个即构建失败，未来新增模块需显式加入。Python 源码、CLI、服务与默认配置
原样复制。依赖为 `python3`、`python3-pil`、`fonts-dejavu-core`、`fonts-wqy-microhei`，
以及服务维护工具所在的 `init-system-helpers (>= 1.56)`。DejaVu 用于英文/数字，
WQY MicroHei 用于中文短标签；两者都直接由 Pillow 读取，不需要启动字体服务。
`Architecture: all` 表示包内没有架构相关二进制；实际显示仍需要 E87N 板型及其内核
NV3007 framebuffer/背光支持。modules-load 配置只声明 `fb_nv3007`，不提供或立即加载模块。

包包含整个现有 Python CLI/诊断实现，但明确不包含旧 `provision.py`、provision 服务、
`image-defaults.sh`、`system-levelpasswordconfig`、SSH/网络/账户配置、固件、内核或
其他 board-support 配置，也不扫描仓库 `docs/`。构建不依赖 overlay 外的 LICENSE、
NOTICE 或 docs；许可证声明位于 packaging 内。没有风扇控制守护进程；
温控与 PWM 仍由内核控制。打包不代表板卡显示、风扇或新镜像已经完成实机验证。

## 普通用户安装与升级

Display Release 中的 `e87n-display_<version>_all.deb` 是**已经启动的 Debian 系统的独立软件包**，
不是 U-Boot 固件，也不是整盘镜像。安装或升级它不会改写 U-Boot、内核、DTB、initrd、
rootfs 分区或风扇控制器；屏幕硬件仍必须由 E87N 内核提供 `fb_nv3007` 和背光节点。

先从目标 Display Release 下载 `.deb`，按该 Release 正文校验 SHA-256，然后上传到设备。
不要从 Firmware Release 寻找 deb，也不需要让 display 版本与固件版本相同。设备默认通过
DHCP 获取地址，下面的 `<设备IP>` 替换成路由器租约中的地址：

```sh
# macOS
shasum -a 256 e87n-display_<版本>_all.deb
scp e87n-display_<版本>_all.deb root@<设备IP>:/tmp/

# Linux
sha256sum e87n-display_<版本>_all.deb
scp e87n-display_<版本>_all.deb root@<设备IP>:/tmp/
```

登录设备后安装或升级：

```sh
ssh root@<设备IP>
dpkg-deb -f /tmp/e87n-display_<版本>_all.deb Package Version Architecture
apt-get install -y /tmp/e87n-display_<版本>_all.deb
systemctl daemon-reload
systemctl restart e87n-display.service
```

如果 APT 报告依赖缺失，先更新 Debian 软件源再重试；正常的独立升级不需要重新刷机：

```sh
apt-get update
apt-get install -y /tmp/e87n-display_<版本>_all.deb
```

升级过程中如果 `dpkg` 询问是否保留 `/etc/e87n/display.json`，已有自定义配置时选择
**保留当前本地版本**。包升级后检查安装状态和服务：

```sh
dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
systemctl is-enabled e87n-display.service
systemctl is-active e87n-display.service
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
```

以上命令应分别看到 `e87n-display <版本> install ok installed`、`enabled`、`active`，
且 `NRestarts=0`、`ExecMainStatus=0`。安装包只负责用户空间程序、配置和 systemd 服务；
如果 `/dev/fb0` 不存在或名称不是 `fb_nv3007`，应先检查内核/DTB，而不是反复安装 `.deb`。

保留旧版 `.deb` 即可回退显示程序；回退不会回退内核或整机系统：

```sh
apt-get install -y --allow-downgrades /tmp/e87n-display_<旧版本>_all.deb
systemctl restart e87n-display.service
dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
```

如果只是暂时停止屏幕，可使用 `e87nctl display off`；这不会停止风扇，也不会禁用内核
thermal governor。重新显示使用 `e87nctl display on`。只有确定不再需要用户空间程序时才使用
`apt-get remove e87n-display`；不要用 `purge` 作为普通的升级或故障排查步骤。

## 修改主题、页面和亮度

推荐使用 `e87nctl` 修改配置。它会校验取值、加锁并原子写入
`/etc/e87n/display.json`，同时立即应用背光设置；不建议用编辑器直接覆盖 JSON。

```sh
# 三种配色主题；数据语义不变，布局按有效遥测自动收缩
e87nctl display theme dark       # 默认深色工业配色
e87nctl display theme aurora     # 黑紫霓虹配色
e87nctl display theme light      # 明亮高对比配色

# 固定显示某一页，并关闭自动轮换
e87nctl display rotation off
e87nctl display screen overview

# 设置亮度、数据刷新间隔
e87nctl display brightness 20
e87nctl display refresh 2

# 开启八页轮换，每 3 秒切换一次
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display rotation-seconds 3
e87nctl display rotation on

# 查看最终配置
e87nctl display config
```

可用页面为 `overview`、`cpu`、`memory`、`thermal`、`fan`、`network`、`traffic`、
`storage`；亮度范围为 `0..100`，数据刷新
和页面轮换间隔范围均为 `2..60` 秒。`refresh_seconds` 控制同一页面多久重新采样，
`rotation_seconds` 控制页面多久切换，两者互不冲突。轮换页面列表必须是 1–8 个不重复页面。
全部八页都可配置，但自动轮换会按实时数据过滤每个不可用页面；缺失遥测对应的卡片、行或
页面不保留空占位。固定选择的页面暂时不可用时显示 `overview`，数据恢复后自动回到配置的
固定页面。当前设备没有可用存储温度遥测，因此 `storage` 仍可配置，但运行时会自动跳过。
保留的 1.2 配置中 `dual`、`single`、`compact` 会分别迁移到 `dark`、`light`、`aurora`，
下一次通过 `e87nctl` 保存设置时写回新名称。

网口可见性独立于主题：`carrier=0` 时，无论是否残留地址或 RX/TX 计数都隐藏；carrier 未知
时，仅有效 IPv4 或全局 IPv6 能让网口显示，link-local IPv6 单独存在不够。两个有效网口
并排显示，只有一个有效网口时卡片使用整行全宽布局。

配置文件中的 8 个字段如下：

| 字段 | 类型/范围 | 作用 |
| --- | --- | --- |
| `enabled` | `true` / `false` | 是否绘制并保持屏幕开启 |
| `brightness_percent` | `0..100` | 屏幕亮度百分比 |
| `screen` | 八个已定义页面之一 | 配置的固定页面；不可用时临时回退 overview |
| `refresh_seconds` | `2..60` | 同一页面的数据刷新周期 |
| `theme` | `dark` / `aurora` / `light` | 只改变配色，不改变数据语义或可用性规则 |
| `rotation_enabled` | `true` / `false` | 是否自动轮换页面 |
| `rotation_seconds` | `2..60` | 页面轮换周期 |
| `rotation_screens` | 1–8 个不重复页面 | 页面轮换顺序 |

显示服务会在运行中重新读取配置，因此大多数设置无需重启即可生效。为了让切换时机明确，
也可以在修改后执行：

```sh
systemctl restart e87n-display.service
```

如果必须手工编辑配置，必须保留下面 8 个键且使用合法 JSON；修改后先验证，再应用：

```sh
vi /etc/e87n/display.json
e87nctl display config
e87nctl display apply
systemctl restart e87n-display.service
```

手工编辑时不要删除 `rotation_enabled`、`rotation_seconds` 或 `rotation_screens`，也不要
写入未定义的主题/页面名称。`e87nctl display config` 返回错误时，先修复 JSON，再重启服务。

## 安装后的真实设备验收

下面的检查应在已经启动的目标板上执行；预览 PNG 或 QEMU 不能替代 `/dev/fb0` 实测：

```sh
e87nctl doctor
e87nctl display config
cat /sys/class/graphics/fb0/name
ls -l /dev/fb0
systemctl status e87n-display.service --no-pager
journalctl -u e87n-display.service -b --no-pager -n 100
```

主题切换验收：

```sh
for theme in dark aurora light; do
  e87nctl display rotation off
  e87nctl display theme "$theme"
  systemctl restart e87n-display.service
  e87nctl display config
  systemctl is-active e87n-display.service
done
```

轮换验收：

```sh
e87nctl display pages overview,cpu,memory,thermal,fan,network,traffic,storage
e87nctl display rotation-seconds 3
e87nctl display rotation on
systemctl restart e87n-display.service
e87nctl display config
systemctl show e87n-display.service -p NRestarts -p ExecMainStatus
```

观察实体屏幕至少 30 秒，应按配置顺序循环；缺失必要遥测的页面会被跳过，当前设备因没有
存储温度而应跳过 `storage`。还应拔掉一个网口，确认 `carrier=0` 的端口即使有残留地址/计数
也隐藏，单端口卡片变为全宽；carrier 未知且只有 link-local IPv6 时也不应显示。固定选择
不可用页面时应临时看到 `overview`，恢复遥测后自动返回配置页。若页面不变，先确认
`rotation_enabled=true`、页面列表有效、服务为 `active`，再查看 journal；不要把“配置文件
写入成功”误认为 framebuffer 已正常写入。

## 安装、预装与维护

在已启动的目标 Debian 系统安装或升级：

```sh
sudo apt-get install ./e87n-display_1.3.1-1_all.deb
dpkg-query -W -f='${Package} ${Version} ${Status}\n' e87n-display
dpkg-query -L e87n-display
systemctl status e87n-display.service
```

在目标 chroot 的接入命令为（`/tmp/e87n-debs` 应为本次构建的专用空输出目录）：

```sh
bash /tmp/overlay/e87n-package/scripts/build-display-deb.sh --output-dir /tmp/e87n-debs
apt-get -y --no-install-recommends install /tmp/e87n-debs/e87n-display_*_all.deb
```

APT 负责解析本地包依赖。安装期间必须保留可执行的 `/usr/sbin/policy-rc.d`，使其对
服务启动/重启返回 **101**；应保存并恢复镜像框架原有策略，不能覆盖后直接删除。
不要把构建主机的 `/run/systemd` 挂入 chroot。包本身不创建、覆盖或删除 policy-rc.d。
不要继续复制同一组文件或再次创建 enable 符号链接；安装成功后这些文件应由 dpkg 管理。
接入方需用目标 dpkg 数据库核对包为 `install ok installed`，不能仅检查文件存在。

首次 configure 用 `deb-systemd-helper` 启用下一次启动时的显示服务。正常 chroot
没有 `/run/systemd/system`，不执行运行态服务操作；即使存在该目录，启动/重启/停止
仍经 `deb-systemd-invoke` 遵守 policy-rc.d。`DPKG_ROOT` 非空的离线安装也不接触
主机运行态 systemd。维护脚本不运行 `e87nctl`、模块加载或任何硬件写操作。

运行中安装会尝试 start，升级在 unpack 完成后尝试 restart；Debian helper 保留管理员
disable/mask 的选择，禁用且未运行的服务不会被升级启动。手动启动的禁用服务可能重启。
显示或 systemd 启动失败不阻塞 dpkg configure，应通过 `systemctl status` / journal
确认服务状态。升级期间清理旧 Python 字节码，避免旧缓存随卸载残留。

两份 `/etc` 配置由 dpkg conffile 机制管理，维护脚本不会重写用户设置。若用户和新包
同时改变同一配置，交互安装由 dpkg 询问；自动构建/升级应明确保留旧配置：

```sh
sudo apt-get -y -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold install ./e87n-display_1.3.1-1_all.deb
sudo apt-get remove e87n-display  # 停止显示；保留配置及服务启用状态记录
sudo apt-get purge e87n-display   # 删除包的 conffile 与 helper 状态
```

remove 后重新安装会恢复此前的启用/禁用状态。purge 不递归删除 `/etc/e87n`、
`/var/lib/e87n` 或管理员的 systemd override/mask；其他组件数据不属于本包。
卸载不会卸载内核驱动或改变内核风扇控制。

维护行为依据 Debian 的 [deb-systemd-helper](https://manpages.debian.org/trixie/init-system-helpers/deb-systemd-helper.1p.en.html)、
[deb-systemd-invoke](https://manpages.debian.org/trixie/init-system-helpers/deb-systemd-invoke.1p.en.html)
和 [maintainer script 生命周期](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html)。

## 专用测试

```sh
python3 -B tests/test-display-package.py
```

跨平台部分检查 shell 语法、CLI 错误与维护脚本分支，所有运行态服务工具均使用夹具。
Linux 上有 dpkg 时还构建、解包实际 `.deb`，核对版本、依赖、完整内容、权限和 conffile。
root 权限下还在新建临时根目录运行 dpkg 安装/升级/remove/reinstall/purge，验证修改过的
配置保留、服务 enable/disable/mask 状态及 Python 缓存清理。使用
`--root --force-script-chrootless --force-depends` 仅为隔离测试：夹具根目录没有真实依赖包，
不访问主机 dpkg 数据库，不启动显示服务。另以真实 `deb-systemd-invoke` 和拒绝策略验证
policy-rc.d 返回 101 时无 systemctl 调用。缺少 dpkg、Debian helper 或 root 时对应项目
明确 SKIP，不可将跳过项计作通过，也不应在生产安装中使用测试的 force 参数。
