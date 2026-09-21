# E87N 真实板卡显示验收（2026-09-21）

本记录包含同一台真实 E87N 的连续验证：早先的基础 framebuffer 验收使用
`e87n-display 1.1.5-1`，随后升级到 `1.2.0-1` 和 `1.3.0-1`；2026 年 9 月 21 日又升级到
`1.3.1-1`，验证动态网口隐藏、单口全宽、缺失遥测过滤、固定页面回退和八页轮换配置；随后
升级到只更新包内操作文档的 `1.3.2-1`，验证 Release 附件、配置保留和服务重启状态。
历史结果保留在下文，本文件的最新包安装结论以 `1.3.2-1` 为准，实体界面视觉结论仍来自
渲染代码相同的 `1.3.1-1` 验收。

## 设备与软件

| 项目 | 实测值 |
| --- | --- |
| 设备地址 | `192.168.20.201` |
| 系统 | Debian 13 Trixie / Armbian unofficial |
| 内核 | `6.18.52-current-edgepi-e87n` |
| framebuffer | `fb_nv3007`、`428×142`、16-bit RGB565 |
| 显示包 | `e87n-display 1.3.2-1`（由 `1.3.1-1` 独立升级） |
| 中文字体 | `fonts-wqy-microhei 0.2.0-beta-4` |
| 网口 | `eth0`、`eth1` |

## `e87n-display 1.3.2-1` 包与升级验收

- Display workflow [35614984795](https://github.com/baozaodetudou/EDGEPI-E87N/actions/runs/35614984795)
  的 `validate`、`display`、`release` 三个 job 全部成功，绑定源码提交 `1886095` 和正式 tag
  `e87n-display-v1.3.2-1`。
- Display Release 只包含 `e87n-display_1.3.2-1_all.deb`，SHA-256 为
  `78b88a25dc7eccade9f35f4c101a90c72ea71bb92a3ac6ec5c37931927d2cd05`；设备端再次核对摘要
  一致，包元数据为 `Package: e87n-display`、`Version: 1.3.2-1`、`Architecture: all`。
- 使用 APT 和 `--force-confdef --force-confold` 从 `1.3.1-1` 原地升级。升级前后的
  `e87nctl display config` 输出逐字段一致：`aurora`、20% 亮度、2 秒刷新、八页轮换列表、
  3 秒轮换、`overview` 起始页和 `enabled=true` 均保留。
- 升级后包状态为 `install ok installed`，服务保持 `enabled`、`active`，等待 5 秒后仍为
  `NRestarts=0`、`ExecMainStatus=0`，framebuffer 名称仍为 `fb_nv3007`；安装后的 journal
  没有 warning 或更高等级记录。
- 包内 `README.Debian` 已包含累计流量、配置热加载、`brightness 0` 与 `display off` 的区别、
  固定页面和卸载前关屏说明，并指向完整在线用户指南。
- `1.3.2-1` 没有修改 `board-support/e87n/display.py` 或硬件采集代码，因此不重复声明新的
  光学或页面像素验收；下节 `1.3.1-1` 的实体面板和 framebuffer 结果继续适用于相同渲染代码。
- 本轮只升级显示 deb，没有刷写 U-Boot、内核、DTB 或整机镜像。

## `e87n-display 1.3.1-1` 验收结果

- 在板端从当前源码构建 `e87n-display_1.3.1-1_all.deb`，SHA-256 为
  `49977cdc18449fe66e55d1d9e25eacbefbfdaaba8c35b80d5ddf519d349a7333`；包元数据为
  `Package: e87n-display`、`Version: 1.3.1-1`、`Architecture: all`。
- 使用 APT 从 `1.3.0-1` 原地升级并保留 `/etc/e87n/display.json`，随后配置
  `overview,cpu,memory,thermal,fan,network,traffic,storage` 八页、3 秒轮换和 `aurora`
  主题。最终配置保持 `rotation_enabled=true`、20% 亮度和 2 秒数据刷新。
- 实时快照中 `eth0` 为 `carrier=1`、IPv4 `192.168.20.201`；`eth1` 为 `carrier=0`，仍有
  link-local IPv6 和历史 RX/TX 计数。新版筛选后只有 `eth0`，总览和网络页只渲染全宽
  `网口1` 卡片，不再显示空的 `eth1`。风扇页显示“自动”、`L1/3`、`50%`、`step_wise`
  和“无测速”，不显示内部值 `kernel-thermal`。
- 板上没有存储温度遥测；实时可用页为 `overview`、`cpu`、`memory`、`thermal`、`fan`、
  `network`、`traffic`。固定选择 `storage` 时 daemon 页面集合为 `overview`，恢复八页轮换后
  `storage` 自动跳过。
- 对上述 7 个可用页面逐一测试 `dark`、`aurora`、`light` 三种主题，共 21 组。每组均从
  真实 `/dev/fb0` 读回完整 `121552` 字节（stride `856`）RGB565 帧，21 个 SHA-256 均不同，
  且均不是全零帧。
- 板端回归通过：`test-display.py` 57 项、`test-hardware.py` 58 项、`test-doctor.py` 44 项、
  `test-verify-display-fan.py` 32 项、`test-display-package.py` 19 项、
  `test-ci-prepare-release.py` 11 项；`scripts/ci-display-regressions.sh` 通过。
- 最终服务为 `active (running)`、`NRestarts=0`、`ExecMainStatus=0`；安装及页面/主题切换后的
  10 分钟 journal 没有 warning 或更高等级记录。
- 现场操作者在实体设备上确认 `1.3.1-1` 屏幕显示正常；该确认覆盖实际面板可见性和本轮
  界面结果，不等同于留存照片或使用仪器量测颜色、亮度与可视角度。
- 本轮只升级显示 deb，没有刷写 U-Boot、内核、DTB 或整机镜像。

以上 framebuffer 读回与现场确认共同证明新版页面已在实体屏幕正常显示；本轮未留存屏幕
照片，也没有进行仪器级颜色、亮度或可视角度测量。

## `e87n-display 1.3.0-1` 验收结果

- 在板端从当前源码构建 `e87n-display_1.3.0-1_all.deb`，SHA-256 为
  `a8e4f401ed3f1649051e9ffc7b65bb274c02d93807db25b415cf52ce4a9a031c`；包元数据为
  `Package: e87n-display`、`Version: 1.3.0-1`、`Architecture: all`。
- 通过 APT 从 `1.2.0-1` 原地升级，保留管理员修改过的 `/etc/e87n/display.json`。旧主题
  `compact` 在运行时正确迁移为 `aurora`；最终写回 `aurora`，并保留原四页轮换顺序、
  3 秒轮换和 20% 亮度。
- `dark`、`aurora`、`light` 三种配色与 `overview`、`cpu`、`memory`、`thermal`、
  `fan`、`network`、`traffic`、`storage` 八个页面逐一组合测试，共 24 组。每组均从真实
  `/dev/fb0` 读回完整的 `121552` 字节 RGB565 帧，24 个 SHA-256 均不同。
- 显示/硬件聚焦回归在板端通过：`test-hardware.py` 58 项、`test-display.py` 52 项、
  `test-doctor.py` 44 项、`test-verify-display-fan.py` 32 项、`test-display-package.py` 19 项。
- Display workflow 静态验证在板端使用 actionlint `1.7.12` 和 ShellCheck `0.10.0` 通过；
  workflow 41 项、Release prepare 11 项、Release publish 13 项、build config 5 项均通过，
  `ci-build.sh display` 成功构建 `1.3.0-1`。
- 最终服务为 `active (running)`、`NRestarts=0`、`ExecMainStatus=0`；本轮切换期间 journal
  没有 error 级别记录。
- 本轮只升级显示 deb，没有刷写 U-Boot、内核、DTB 或整机镜像。

以上 framebuffer 读回证明服务写入了各页面的不同像素内容，但不等于摄像头或肉眼确认
LCD 面板的颜色、方向和清晰度。

## `e87n-display 1.2.0-1` 历史结果

- `e87n-display.service`: `active (running)`。
- `NRestarts=0`，`ExecMainStatus=0`。
- `dual`、`single`、`compact` 三种主题均在实体设备上切换，并在每次切换后重启服务确认
  配置加载成功。
- `overview`、`network`、`thermal`、`storage` 四页已配置为每 3 秒轮换；实体 `/dev/fb0`
  连续观察 15 秒期间内容哈希持续变化，服务保持 active 且没有重启。
- 当前最终配置为 `theme=compact`、`rotation_enabled=true`、`rotation_seconds=3`，便于
  继续观察多页面效果。
- `eth0` 显示真实 IPv4 `192.168.20.201`；`eth1` 显示真实 IPv6 链路地址。
- `overview`、`thermal`、`network`、`storage` 四个页面都从真实 `/dev/fb0` 读回，尺寸均为
  `428×142`，中文、颜色和面板边界正常。
- Overview 已确认显示 CPU、内存、温度、风扇 PWM，以及固定的双网口卡片；不显示 RPM。
- Thermal 已确认显示 CPU/PHY 温度、自动模式、cooling level、PWM 和内核策略。
- `e87nctl display off/on`、亮度 20%、页面切换和 `e87nctl fan test 1 1` 通过；风扇测试后
  恢复原档位 `L1/3`。
- 实测风扇：`pwm-fan`、`step_wise`、PWM `128/255 = 50%`，没有 `fan1_input`，因此不把
  PWM 推断为 RPM。

## `e87n-display 1.2.0-1` 历史修复

1. 接受 E87N fbtft 的 `var_screeninfo.nonstd=1` RGB565 标记。
2. 显示服务的 systemd 沙箱从仅 `AF_UNIX` 调整为 `AF_UNIX AF_INET`，只满足本地
   `SIOCGIFADDR` 读取 IPv4；程序不连接、绑定、发送网络数据，也不打开 AF_INET6。
3. 默认包依赖 `fonts-wqy-microhei`，镜像静态审计和 CI 验证同时检查该字体。
4. 风扇 UI 保留内核 thermal governor、档位和 PWM 百分比，不伪造测速数据。

## `e87n-display 1.2.0-1` 独立升级记录

本次没有刷 U-Boot、内核或整机固件，只在已经启动的 Debian 上上传并安装独立屏幕包：

```text
设备：192.168.20.201
本地构建包：e87n-display_1.2.0-1_all.deb
SHA-256：6f30e49c0e96940dea49ff098ad8d49526bbb35894061d254a0a4302bf6bbf07
```

设备端确认：

```text
e87n-display 1.2.0-1 install ok installed
e87n-display.service enabled / active
NRestarts=0
ExecMainStatus=0
fb_nv3007
```

主题切换使用 `e87nctl display theme dual|single|compact` 完成；轮换使用
`e87nctl display pages overview,network,thermal,storage`、
`e87nctl display rotation-seconds 3` 和 `e87nctl display rotation on` 完成。以后只需升级
`.deb` 或修改 `/etc/e87n/display.json`，不需要重新刷写 U-Boot 固件；操作手册见
[独立屏幕包说明](DISPLAY-PACKAGE.md) 和[小白刷机指南](QUICKSTART-BEGINNER.md)。

## 验证边界

本记录证明真实板卡上的系统服务、字体、framebuffer 写入、遥测、主题切换和页面轮换配置
可用，现场操作者也已确认实体屏幕显示正常。当前没有留存摄像头照片或面板光学测量数据，
因此不据此宣称颜色、亮度、可视角度达到量化指标；长期稳定性仍需持续观察。
