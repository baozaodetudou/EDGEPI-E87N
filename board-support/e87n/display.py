"""Debian-native, read-only telemetry renderer for the E87N NV3007 fbdev.

Runtime dependencies: Python 3, python3-pil, fonts-dejavu-core and
fonts-wqy-microhei. Install this
module beside hardware.py in the e87n package (or put board-support on
PYTHONPATH for a source checkout). The installed Debian package supports
``python3 -I -m e87n.display --daemon`` without PYTHONPATH. No OpenWrt
executables or external image assets are used.

    python3 -m e87n.display --preview /tmp/overview.png --screen overview
    python3 -m e87n.display --daemon

Preview uses only the in-memory fixture, Pillow and DejaVu fonts. It never
imports hardware or reads /dev, /sys, /proc or the live display configuration.
The daemon lazily imports ``from . import hardware as hw``, calls
``hw.load_display_config()`` on EVERY iteration (including disabled ones), and
calls ``hw.snapshot()`` once per enabled iteration. The hardware module owns
strict root-owned/no-symlink/exact-key validation of /etc/e87n/display.json and
the integer refresh interval 2..60. Its missing-file defaults are enabled=true,
brightness_percent=20, screen=overview, refresh_seconds=2.
Invalid configuration and I/O errors are fatal (stderr, status 1); systemd
should use Restart=on-failure to reopen/revalidate the device after failure.
The control service owns brightness; the kernel alone controls the fan.
This module NEVER writes backlight, fan or sysfs files.
Disabling skips drawing; physical blanking is the control service's job.
An offline host may supply --preview-font-dir containing DejaVuSans.ttf and
DejaVuSans-Bold.ttf. This override is unavailable in daemon mode.
"""

import argparse
from array import array
from collections import deque
from collections.abc import Mapping
import ctypes
from dataclasses import dataclass
import fcntl
from functools import lru_cache, partial
import ipaddress
import math
import os
from pathlib import Path
import stat
import sys
import time

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 428, 142
SCREENS = ("overview", "thermal", "network", "storage")
COMPATIBLE_PATH = Path("/sys/firmware/devicetree/base/compatible")
FONT_DIRECTORY = Path("/usr/share/fonts/truetype/dejavu")
CJK_FONT_PATH = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
MAX_FB_BYTES = 16 * 1024 * 1024
U32 = ctypes.c_uint32
U16 = ctypes.c_uint16


class FbBitfield(ctypes.Structure):
    _fields_ = [("offset", U32), ("length", U32), ("msb_right", U32)]


class FbFixScreeninfo(ctypes.Structure):
    # Linux UAPI linux/fb.h, native alignment. In particular, unsigned long is
    # EIGHT bytes on arm64; _pack_=1 or a fixed 32-bit pointer ABI is incorrect.
    _fields_ = [
        ("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
        ("smem_len", U32), ("type", U32), ("type_aux", U32), ("visual", U32),
        ("xpanstep", U16), ("ypanstep", U16), ("ywrapstep", U16),
        ("line_length", U32), ("mmio_start", ctypes.c_ulong),
        ("mmio_len", U32), ("accel", U32), ("capabilities", U16),
        ("reserved", U16 * 2),
    ]


class FbVarScreeninfo(ctypes.Structure):
    _fields_ = [
        ("xres", U32), ("yres", U32), ("xres_virtual", U32),
        ("yres_virtual", U32), ("xoffset", U32), ("yoffset", U32),
        ("bits_per_pixel", U32), ("grayscale", U32),
        ("red", FbBitfield), ("green", FbBitfield),
        ("blue", FbBitfield), ("transp", FbBitfield),
        ("nonstd", U32), ("activate", U32), ("height", U32), ("width", U32),
        ("accel_flags", U32), ("pixclock", U32), ("left_margin", U32),
        ("right_margin", U32), ("upper_margin", U32), ("lower_margin", U32),
        ("hsync_len", U32), ("vsync_len", U32), ("sync", U32), ("vmode", U32),
        ("rotate", U32), ("colorspace", U32), ("reserved", U32 * 4),
    ]


class DisplayError(RuntimeError):
    """A configuration, framebuffer or telemetry contract failure."""


@dataclass(frozen=True)
class FramebufferLayout:
    stride: int
    xoffset: int
    yoffset: int
    virtual_width: int
    virtual_height: int
    memory_bytes: int
    red_offset: int
    green_offset: int
    blue_offset: int

    @property
    def start(self):
        return self.yoffset * self.stride + self.xoffset * 2


def validate_framebuffer(fix, var):
    """Validate BEFORE allocation/packing/writing; arithmetic uses Python ints."""
    if bytes(fix.id) != b"fb_nv3007":
        raise DisplayError("framebuffer ID must be fb_nv3007")
    if (var.xres, var.yres) != (WIDTH, HEIGHT):
        raise DisplayError("framebuffer visible geometry must be 428x142")
    if (fix.type, fix.type_aux, fix.visual) != (0, 0, 2):
        raise DisplayError("framebuffer must be packed-pixel truecolor")
    # fbtft sets FB_NONSTD_HAM (1) on its fbdev var_screeninfo even when the
    # actual memory layout is the normal packed RGB565 layout. This is the
    # upstream fbtft ABI marker, not a request for HAM decoding. Accept that
    # one known marker for the E87N NV3007 driver, while still rejecting all
    # other non-standard formats and every mode-setting flag.
    if var.bits_per_pixel != 16 or var.grayscale or var.nonstd not in (0, 1) or var.vmode:
        raise DisplayError("framebuffer must be packed progressive 16-bit RGB")
    if var.transp.length or var.transp.msb_right or var.transp.offset > 16:
        raise DisplayError("RGB565 must not have an alpha bitfield")
    mask = 0
    for field, length in ((var.red, 5), (var.green, 6), (var.blue, 5)):
        if field.length != length or field.msb_right or field.offset > 16 - length:
            raise DisplayError("invalid RGB565 bitfield")
        field_mask = ((1 << length) - 1) << field.offset
        if mask & field_mask:
            raise DisplayError("overlapping RGB565 bitfields")
        mask |= field_mask
    if mask != 0xFFFF:
        raise DisplayError("RGB565 bitfields must cover exactly 16 bits")
    if var.xres_virtual < WIDTH or var.yres_virtual < HEIGHT:
        raise DisplayError("virtual framebuffer is smaller than the visible screen")
    if var.xoffset + WIDTH > var.xres_virtual or var.yoffset + HEIGHT > var.yres_virtual:
        raise DisplayError("visible offsets exceed virtual framebuffer bounds")
    if fix.line_length % 2 or fix.line_length < var.xres_virtual * 2:
        raise DisplayError("invalid framebuffer line stride")
    if not 0 < fix.smem_len <= MAX_FB_BYTES:
        raise DisplayError("invalid or excessive framebuffer memory size")
    # Never wrap a 32-bit stride/product, even when the ioctl supplied UINT_MAX.
    if fix.line_length * var.yres_virtual > fix.smem_len:
        raise DisplayError("virtual framebuffer exceeds available memory")
    end = (var.yoffset + HEIGHT - 1) * fix.line_length + (var.xoffset + WIDTH) * 2
    if end > fix.smem_len:
        raise DisplayError("visible framebuffer exceeds available memory")
    return FramebufferLayout(
        fix.line_length, var.xoffset, var.yoffset, var.xres_virtual,
        var.yres_virtual, fix.smem_len, var.red.offset, var.green.offset,
        var.blue.offset,
    )


def pack_rgb565(image, layout, byteorder=sys.byteorder):
    """Quantize RGB using the verified bitfields and native fbdev byte order."""
    if image.size != (WIDTH, HEIGHT):
        raise DisplayError("rendered image must be 428x142")
    if byteorder not in ("little", "big"):
        raise ValueError("byteorder must be little or big")
    rgb = image.convert("RGB").tobytes()
    pixels = array("H", (
        ((r >> 3) << layout.red_offset)
        | ((g >> 2) << layout.green_offset)
        | ((b >> 3) << layout.blue_offset)
        for r, g, b in zip(rgb[0::3], rgb[1::3], rgb[2::3])
    ))
    if pixels.itemsize != 2:
        raise DisplayError("platform has no native 16-bit unsigned short")
    if byteorder != sys.byteorder:
        pixels.byteswap()
    return pixels.tobytes()


def _ioctl_struct(fd, request, structure):
    buffer = bytearray(ctypes.sizeof(structure))
    fcntl.ioctl(fd, request, buffer, True)
    return structure.from_buffer_copy(buffer)


def _check_character_device(info):
    if not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) != 29:
        raise DisplayError("framebuffer must be a Linux fbdev character device (major 29)")


class Framebuffer:
    """Write only visible rows; never mode-set, pan, blank, or access MMIO.

    pwrite uses the fbdev write path (including fbtft deferred refresh), avoiding
    mapped physical addresses and retaining the kernel's I/O error reporting.
    Errors propagate to the daemon; there is no in-process recovery loop.
    """

    def __init__(self, path="/dev/fb0", compatible_path=COMPATIBLE_PATH):
        self.fd = None
        compatible = Path(compatible_path).read_bytes()
        if len(compatible) > 4096 or not compatible.endswith(b"\0") or b"edgepi,e87n" not in compatible.split(b"\0"):
            raise DisplayError("device-tree compatible must contain edgepi,e87n")
        before = os.stat(path, follow_symlinks=False)
        _check_character_device(before)
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            after = os.fstat(self.fd)
            _check_character_device(after)
            if (before.st_dev, before.st_ino, before.st_rdev) != (
                after.st_dev, after.st_ino, after.st_rdev
            ):
                raise DisplayError("framebuffer device changed while opening")
            self.layout = self._read_layout()
        except BaseException:
            self.close()
            raise

    def _read_layout(self):
        fix = _ioctl_struct(self.fd, FBIOGET_FSCREENINFO, FbFixScreeninfo)
        var = _ioctl_struct(self.fd, FBIOGET_VSCREENINFO, FbVarScreeninfo)
        return validate_framebuffer(fix, var)

    def _write_all(self, data, offset):
        data = memoryview(data)
        while data:
            try:
                written = os.pwrite(self.fd, data, offset)
            except InterruptedError:
                continue
            if written <= 0 or written > len(data):
                raise DisplayError("framebuffer write made invalid progress")
            offset += written
            data = data[written:]

    def draw(self, image):
        if self.fd is None:
            raise DisplayError("framebuffer is closed")
        # Catch mode/offset changes before touching memory. The service must own
        # this fbdev exclusively; ioctls cannot lock out an external mode setter.
        if self._read_layout() != self.layout:
            raise DisplayError("framebuffer mode changed; restart required")
        data = pack_rgb565(image, self.layout)
        row_bytes = WIDTH * 2
        if self.layout.stride == row_bytes:
            self._write_all(data, self.layout.start)
        else:
            for row in range(HEIGHT):
                self._write_all(
                    data[row * row_bytes:(row + 1) * row_bytes],
                    self.layout.start + row * self.layout.stride,
                )

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


def load_display_config():
    """Delegate ALL defaults, ownership/path checks and parsing to hardware."""
    from . import hardware as hw
    return hw.load_display_config()


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _nonnegative(value):
    return _finite_number(value) and value >= 0


def _temperature(value):
    return "{:.1f} C".format(value / 1000) if _finite_number(value) else "--"


def _integer(value):
    return str(value) if type(value) is int and value >= 0 else "--"


def _bytes(value):
    if not _nonnegative(value):
        return "--"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        if value < 1024 or unit == "EiB":
            return ("{:.0f}" if unit == "B" else "{:.1f}").format(value) + " " + unit
        value /= 1024


def _uptime(value):
    if not _nonnegative(value):
        return "--"
    minutes = int(value) // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return "{}d {:02}h".format(days, hours) if days else "{}h {:02}m".format(hours, minutes)


def _safe_text(value):
    if not isinstance(value, str) or not value.strip():
        return "--"
    # Sensor/interface names are data, including controls or unusually long names.
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in value[:120])


def _items(snapshot, key):
    value = snapshot.get(key)
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def _overview_address(snapshot):
    """Return one compatibility address for callers that still use this helper.

    The renderer now shows two fixed LAN cards instead of selecting one global
    address.  Keep this small helper for diagnostics and older integrations.
    """
    candidates = []
    for index, item in enumerate(_items(snapshot, "network")[:32]):
        carrier = item.get("carrier")
        if type(carrier) in (bool, int) and carrier == 0:
            continue
        ipv6 = item.get("ipv6")
        values = [(item.get("ipv4"), 4)]
        if isinstance(ipv6, (list, tuple)):
            values.extend((value, 6) for value in ipv6[:8])
        for value, version in values:
            if not isinstance(value, str) or len(value) > 39:
                continue
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if (address.version != version or address.is_loopback or address.is_multicast
                    or address.is_unspecified or "%" in value):
                continue
            up = type(carrier) in (bool, int) and carrier == 1
            kind = (2 if address.is_link_local else 0) + (version == 6)
            candidates.append((not up, kind, index, str(address), _safe_text(item.get("name"))))
    if candidates and min(candidates)[1] < 2:
        *_, address, name = min(candidates)
        return name, address
    # fib_trie supplies real local IPv4 even under AF_UNIX-only systemd
    # restrictions. It cannot tell us the interface, so label it honestly.
    local = snapshot.get("local_ipv4")
    if isinstance(local, (list, tuple)):
        for value in local[:32]:
            if not isinstance(value, str) or len(value) > 15:
                continue
            try:
                address = ipaddress.IPv4Address(value)
            except ValueError:
                continue
            if not (address.is_loopback or address.is_unspecified or address.is_multicast):
                return "LOCAL IPv4", str(address)
    if candidates:
        *_, address, name = min(candidates)
        return name, address
    return "NO IP", "--"


def _network_items(snapshot, limit=2):
    """Select stable physical-looking interfaces for the fixed LAN cards.

    Linux names are not guaranteed to be eth0/eth1 on every image.  Prefer
    common physical names, sort them once, and never allow a bridge or loopback
    interface to displace either physical port in the small-screen layout.
    """
    candidates = []
    for item in _items(snapshot, "network")[:32]:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in {"lo", "br-lan", "docker0"}:
            continue
        candidates.append(item)
    physical = [item for item in candidates
                if isinstance(item.get("name"), str)
                and (item["name"].startswith(("eth", "end", "enx", "lan", "wan")))]
    selected = physical or candidates
    return sorted(selected, key=lambda item: _safe_text(item.get("name")))[:limit]


def _network_ip(item):
    """Prefer a valid IPv4 address, then the first valid global/link-local IPv6."""
    value = item.get("ipv4")
    if isinstance(value, str):
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            address = None
        if address is not None and not (address.is_loopback or address.is_unspecified
                                        or address.is_multicast):
            return str(address)
    values = item.get("ipv6")
    if isinstance(values, (list, tuple)):
        for value in values[:8]:
            if not isinstance(value, str) or "%" in value:
                continue
            try:
                address = ipaddress.IPv6Address(value)
            except ValueError:
                continue
            if not (address.is_loopback or address.is_unspecified or address.is_multicast):
                return str(address)
    return "无IP"


def _network_link(item):
    carrier = item.get("carrier")
    if type(carrier) in (bool, int) and carrier in (0, 1):
        return ("在线", "#65d48f") if carrier else ("断开", MUTED)
    return ("--", MUTED)


def _fan_summary(fan):
    """Return a short kernel-controller summary; intentionally never RPM."""
    mode = "自动" if fan.get("mode") == "auto" or fan.get("control") == "kernel-thermal" else "--"
    pwm = fan.get("pwm_percent")
    if pwm is None and type(fan.get("pwm")) is int and 0 <= fan["pwm"] <= 255:
        pwm = (fan["pwm"] * 100 + 127) // 255
    if type(pwm) is int and 0 <= pwm <= 100:
        return "{} {}%".format(mode, pwm)
    return mode


@lru_cache(maxsize=32)
def _font(size, bold=False, font_directory=None):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str((font_directory or FONT_DIRECTORY) / name), size)


def _contains_cjk(value):
    return any("\u3400" <= character <= "\u9fff" for character in str(value))


@lru_cache(maxsize=32)
def _cjk_font(size, font_directory=None):
    candidate = ((font_directory / "wqy-microhei.ttc") if font_directory is not None
                 else CJK_FONT_PATH)
    try:
        return ImageFont.truetype(str(candidate), size)
    except OSError:
        pass
    # Preview fixtures may intentionally provide only DejaVu.  The target
    # package installs fonts-wqy-microhei, while this fallback keeps offline
    # layout tests deterministic on hosts without the optional font.
    return _font(size, False, font_directory)


BACKGROUND = "#08131d"
PANEL = "#102535"
PANEL_ALT = "#0d1e2b"
FOREGROUND = "#f3f7fb"
MUTED = "#8ea3b7"
ACCENT = "#2fe0cb"
GOOD = "#65d48f"
WARNING = "#f2b665"
LINE = "#234052"


def _text(draw, xy, text, size=16, color=FOREGROUND, width=None, bold=False, font_directory=None):
    text = str(text)
    font = _cjk_font(size, font_directory) if _contains_cjk(text) else _font(size, bold, font_directory)
    if width is not None and draw.textbbox((0, 0), text, font=font)[2] > width:
        while text and draw.textbbox((0, 0), text + "...", font=font)[2] > width:
            text = text[:-1]
        text += "..."
    draw.text(xy, text, font=font, fill=color, anchor="lt")


def _panel(draw, box, *, fill=PANEL, outline=LINE, radius=7):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


def _metric(draw, color, x, label, value, *, width=96, value_size=16, font_directory=None):
    _text(draw, (x + 8, 102), label, 10, MUTED, width=width - 16, bold=True,
          font_directory=font_directory)
    _text(draw, (x + 8, 117), value, value_size, color, width=width - 16, bold=True,
          font_directory=font_directory)


def render(snapshot, screen="overview", *, font_directory=None):
    """Pure 428x142 RGB renderer for the E87N low-resolution panel.

    The layout is deliberately information-dense but not a desktop dashboard:
    two fixed LAN cards occupy the middle band, and the bottom band contains
    CPU/RAM/temperature/fan state.  The fan is represented by the kernel
    thermal mode, cooling level and PWM percentage only.  The board has no
    tachometer contract, so RPM is never rendered even if a stray hwmon value
    exists in the snapshot.
    """
    if not isinstance(snapshot, Mapping):
        raise DisplayError("hardware.snapshot() must return a mapping")
    if screen not in SCREENS:
        raise DisplayError("unknown screen: " + str(screen))
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    fan = snapshot.get("fan") if isinstance(snapshot.get("fan"), Mapping) else {}
    title = {
        "overview": "E87N  /  系统",
        "thermal": "温度  /  风扇",
        "network": "网络  /  网口",
        "storage": "存储  /  温度",
    }[screen]

    # A compact, shared header keeps the four screens visually related.
    text(draw, (10, 8), title, 14, ACCENT, width=260, bold=True)
    uptime = _uptime(snapshot.get("uptime_seconds"))
    text(draw, (323, 10), "运行 " + uptime, 11, MUTED, width=95)
    draw.line((10, 30, 418, 30), fill=LINE)

    if screen == "overview":
        text(draw, (10, 37), "设备状态", 10, MUTED, bold=True)
        health = "在线" if _network_items(snapshot) else "无链路"
        health_color = GOOD if health == "在线" else WARNING
        draw.ellipse((104, 39, 111, 46), fill=health_color)
        text(draw, (116, 36), health, 12, health_color, bold=True)
        text(draw, (311, 36), "428 x 142", 10, MUTED, width=107)

        # Fixed physical-port cards. The left/right position never follows the
        # current carrier state, so LAN 1 and LAN 2 remain recognizable.
        ports = _network_items(snapshot)
        for index, x in enumerate((10, 214)):
            item = ports[index] if index < len(ports) else {}
            _panel(draw, (x, 52, x + 194, 94), fill=PANEL_ALT,
                   outline=ACCENT if index == 0 else "#9b8cff")
            label = "网口{}".format(index + 1)
            name = _safe_text(item.get("name"))
            link, link_color = _network_link(item)
            text(draw, (x + 10, 58), label, 11, ACCENT, bold=True)
            draw.ellipse((x + 125, 59, x + 131, 65), fill=link_color)
            text(draw, (x + 136, 56), link, 11, link_color, width=48, bold=True)
            text(draw, (x + 10, 72), _network_ip(item), 16, FOREGROUND, width=174, bold=True)
            text(draw, (x + 10, 87), name, 10, MUTED, width=174)

        total, available = snapshot.get("mem_total_kib"), snapshot.get("mem_available_kib")
        memory = "--"
        if _nonnegative(total) and total > 0 and _nonnegative(available) and available <= total:
            memory = "{:.0f}%".format((total - available) * 100 / total)
        usage = snapshot.get("cpu_usage_percent")
        usage = "{:.0f}%".format(usage) if _nonnegative(usage) and usage <= 100 else "--"
        temperature = _temperature(snapshot.get("cpu_temp_mc"))
        fan_value = _fan_summary(fan)
        metric_width = 99
        for x, label, value, color in (
            (10, "CPU", usage, ACCENT),
            (109, "内存", memory, FOREGROUND),
            (208, "温度", temperature, WARNING),
            (307, "风扇", fan_value, GOOD),
        ):
            _panel(draw, (x, 100, x + metric_width - 4, 137), fill=PANEL, outline=color)
            _metric(draw, color, x, label, value, width=metric_width - 4,
                    # Keep mode, cooling level and PWM percentage visible in
                    # the narrow fan slot instead of truncating the value.
                    value_size=9 if label == "风扇" else 16,
                    font_directory=font_directory)

    elif screen == "thermal":
        # Large temperature readouts on the left; controller facts on the right.
        for x, label, value, color in (
            (10, "CPU", snapshot.get("cpu_temp_mc"), WARNING),
            (128, "PHY", snapshot.get("phy_temp_mc"), ACCENT),
        ):
            _panel(draw, (x, 43, x + 108, 100), fill=PANEL, outline=color)
            text(draw, (x + 10, 50), label, 11, MUTED, bold=True)
            text(draw, (x + 10, 68), _temperature(value), 21, color, width=92, bold=True)
        _panel(draw, (250, 43, 418, 100), fill=PANEL_ALT, outline=GOOD)
        mode = "自动" if fan.get("mode") == "auto" or fan.get("control") == "kernel-thermal" else "--"
        state, maximum = fan.get("state"), fan.get("max_state")
        level = ("L{}/{}".format(state, maximum)
                 if type(state) is int and type(maximum) is int and 0 <= state <= maximum <= 255
                 else "--")
        pwm = fan.get("pwm_percent")
        if pwm is None and type(fan.get("pwm")) is int and 0 <= fan["pwm"] <= 255:
            pwm = (fan["pwm"] * 100 + 127) // 255
        pwm_value = "{}%".format(pwm) if type(pwm) is int and 0 <= pwm <= 100 else "--"
        for y, label, value, color in (
            (49, "模式", mode, GOOD),
            (67, "档位", level, FOREGROUND),
            (85, "PWM", pwm_value, ACCENT),
        ):
            text(draw, (261, y), label, 10, MUTED, bold=True)
            text(draw, (325, y - 2), value, 13, color, width=83, bold=True)
        policy = _safe_text(fan.get("policy"))
        text(draw, (10, 112), "内核策略", 10, MUTED, bold=True)
        text(draw, (108, 109), policy, 13, FOREGROUND, width=126)
        text(draw, (250, 109), "无测速", 11, MUTED, width=168, bold=True)

    elif screen == "network":
        ports = _network_items(snapshot)
        for index, x in enumerate((10, 214)):
            item = ports[index] if index < len(ports) else {}
            _panel(draw, (x, 42, x + 194, 103), fill=PANEL_ALT,
                   outline=ACCENT if index == 0 else "#9b8cff")
            link, link_color = _network_link(item)
            text(draw, (x + 10, 48), "网口{}".format(index + 1), 11, ACCENT, bold=True)
            text(draw, (x + 142, 48), link, 11, link_color, width=42, bold=True)
            text(draw, (x + 10, 65), _network_ip(item), 15, FOREGROUND, width=174, bold=True)
            text(draw, (x + 10, 84), _safe_text(item.get("name")), 10, MUTED, width=60)
            text(draw, (x + 69, 84), "RX " + _bytes(item.get("rx_bytes")), 10, MUTED, width=62)
            text(draw, (x + 133, 84), "TX " + _bytes(item.get("tx_bytes")), 10, MUTED, width=58)
        text(draw, (10, 116), "DHCP / IPv4", 10, MUTED, bold=True)
        text(draw, (101, 113), "链路状态", 11, GOOD, bold=True)
        text(draw, (250, 113), "双网口固定", 10, MUTED, width=168, bold=True)

    else:
        items = _items(snapshot, "storage")
        for index, x in enumerate((10, 214)):
            item = items[index] if index < len(items) else {}
            _panel(draw, (x, 43, x + 194, 91), fill=PANEL_ALT,
                   outline=ACCENT if index == 0 else "#9b8cff")
            text(draw, (x + 10, 50), _safe_text(item.get("name")), 12, ACCENT, width=76, bold=True)
            text(draw, (x + 10, 68), _temperature(item.get("temp_mc")), 20, WARNING, width=174, bold=True)
        text(draw, (10, 105), "NVMe 温度", 11, MUTED, bold=True)
        text(draw, (250, 105), "无虚构数据", 10, MUTED, width=168, bold=True)
        text(draw, (10, 123), "存储传感器可选", 10, FOREGROUND, width=408)
    return image

def preview_snapshot():
    """Deterministic sample data, explicitly not measurements of a live board."""
    return {
        "cpu_usage_percent": 24.0, "cpu_temp_mc": 58750, "phy_temp_mc": 43250,
        "fan": {"state": 2, "max_state": 3, "pwm": 192, "rpm": None, "policy": "step_wise",
                "control": "kernel-thermal", "mode": "auto"},
        "loadavg": [0.42, 0.31, 0.28], "mem_total_kib": 1048576,
        "mem_available_kib": 655360, "uptime_seconds": 183845.0,
        "network": [
            {"name": "end0", "ipv4": "192.0.2.87", "ipv6": ["2001:db8::87"],
             "rx_bytes": 34123456789, "tx_bytes": 8123456789, "carrier": True},
            {"name": "eth1", "rx_bytes": 1234567890, "tx_bytes": 345678901, "carrier": False},
            {"name": "br-lan", "rx_bytes": None, "tx_bytes": None, "carrier": None},
        ],
        "storage": [{"name": "nvme0", "temp_mc": 41250}, {"name": "nvme1", "temp_mc": None}],
    }


def read_snapshot():
    # Preview never imports hardware; disabled iterations only load its config.
    from . import hardware as hw
    return hw.snapshot()


def run_daemon():
    framebuffer = None
    try:
        while True:
            config = load_display_config()
            if config["enabled"]:
                if framebuffer is None:
                    framebuffer = Framebuffer()
                framebuffer.draw(render(read_snapshot(), config["screen"]))
            time.sleep(config["refresh_seconds"])
    finally:
        if framebuffer is not None:
            framebuffer.close()


def _offline_path(path):
    """Resolve symlinks without following one into a hardware filesystem."""
    pending = deque(Path(os.path.abspath(path)).parts[1:])
    resolved = Path("/")
    symlinks = 0
    while pending:
        part = pending.popleft()
        if part == "..":
            resolved = resolved.parent
            continue
        candidate = resolved / part
        if candidate.parts[1:2] in (("dev",), ("sys",), ("proc",)):
            raise DisplayError("preview output must not be a hardware path")
        try:
            # Call os.lstat directly instead of Path.lstat. On Python 3.9,
            # pathlib's cached accessor can retain a bound reference to the
            # original os.lstat and receive the Path object twice when the
            # offline preview test replaces os.lstat with a guarded wrapper.
            info = os.lstat(candidate)
        except FileNotFoundError:
            if pending:
                raise
            return candidate
        if stat.S_ISLNK(info.st_mode):
            symlinks += 1
            if symlinks > 40:
                raise DisplayError("too many preview path symlinks")
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                resolved = Path("/")
            pending.extendleft(reversed(target.parts[1:] if target.is_absolute() else target.parts))
        else:
            resolved = candidate
    return resolved


def _save_preview(image, path):
    """Pin the checked directory and refuse special files/symlink replacement."""
    output = _offline_path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open("/", flags)
    try:
        for part in output.parent.parts[1:]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     0o644, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DisplayError("preview output must be a regular file without hard links")
            os.ftruncate(fd, 0)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                image.save(stream, format="PNG")
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", metavar="OUTPUT.png", type=Path, help="render the offline fixture to PNG")
    mode.add_argument("--daemon", action="store_true", help="render live snapshots using display.json")
    parser.add_argument("--screen", choices=SCREENS, help="preview layout (default: overview)")
    parser.add_argument("--preview-font-dir", "--font-dir", type=Path, metavar="DIRECTORY",
                        help="preview-only directory containing DejaVu Sans regular/bold fonts")
    args = parser.parse_args(argv)
    if args.daemon and (args.screen is not None or args.preview_font_dir is not None):
        parser.error("--screen and --preview-font-dir are for --preview; the daemon uses display.json and Debian fonts")
    try:
        if args.preview is not None:
            output = _offline_path(args.preview)
            font_directory = _offline_path(args.preview_font_dir) if args.preview_font_dir is not None else None
            image = render(preview_snapshot(), args.screen or "overview", font_directory=font_directory)
            _save_preview(image, output)
        else:
            run_daemon()
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print("e87n-display: {}: {}".format(type(error).__name__, error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
