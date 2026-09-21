# e87n-display Debian 软件包

`e87n-display` 是供 E87N minimal Debian 13（Trixie）镜像预装、也可单独升级的
显示与诊断包。版本独立于镜像、Armbian 和内核；默认版本来源为
`packaging/e87n-display/VERSION`。镜像启动器将打包脚本、`packaging/` 与
`board-support/` 按仓库相对结构复制到 `/tmp/overlay/e87n-package/`，customize 在
目标 chroot 中调用该脚本，再通过 APT 安装产物。当前源码已经接入预装流程，
并启用 LCD/背光节点及显示服务；包版本以 `packaging/e87n-display/VERSION` 为准，当前为
`1.2.0-1`。
独立 deb 用于升级和重新安装，不表示基础镜像默认无屏幕。
candidate3 属于此前的 headless 配置，其验收不能替代当前显示配置的重建与实机测试。

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
./scripts/build-display-deb.sh --output-dir ./output/debs --version 1.2.0-1
```

产物严格命名为 `e87n-display_VERSION_all.deb`，例如
`output/debs/e87n-display_1.2.0-1_all.deb`。标准输出包含构建进度，调用方应按版本
构造文件名，不能将整个 stdout 当作路径。版本须为合法 Debian 版本，发布新内容时
递增版本；可用 `dpkg --compare-versions` 检查先后。构建先写独立临时目录，成功后
替换同名产物；失败不覆盖原产物。`dpkg-deb --root-owner-group` 固定包内 root:root
所有权；构建脚本默认 `SOURCE_DATE_EPOCH=0`，CI 明确固定为 0，以便独立 job 的 deb 与 QEMU 实际测试的 deb 精确比对 SHA-256。macOS 缺少 dpkg 时会明确报错，可在已有 Linux VM 中构建。

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

## 安装、预装与维护

在已启动的目标 Debian 系统安装或升级：

```sh
sudo apt-get install ./e87n-display_1.2.0-1_all.deb
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
  -o Dpkg::Options::=--force-confold install ./e87n-display_1.2.0-1_all.deb
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
