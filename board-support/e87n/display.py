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
the integer refresh/rotation intervals and theme/page validation. Its missing-file
defaults are enabled=true, brightness_percent=20, screen=overview,
refresh_seconds=2, theme=dark and rotation disabled.
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
SCREENS = ("overview", "cpu", "memory", "thermal", "fan", "network", "traffic", "storage")
THEMES = ("dark", "aurora", "light")
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


def _valid_ipv4(value):
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        return None
    return str(address)


def _first_local_ipv4(snapshot):
    values = snapshot.get("local_ipv4")
    if isinstance(values, (list, tuple)):
        for value in values[:32]:
            address = _valid_ipv4(value)
            if address is not None:
                return address
    return None


def _items(snapshot, key):
    value = snapshot.get(key)
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def _overview_address(snapshot):
    """Return one compatibility address for callers that still use this helper.

    Keep this small helper for diagnostics and older integrations.
    """
    candidates = []
    for index, item in enumerate(_network_items(snapshot, limit=32)):
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
    local = _first_local_ipv4(snapshot)
    if local is not None:
        return "LOCAL IPv4", local
    if candidates:
        *_, address, name = min(candidates)
        return name, address
    return "NO IP", "--"


def _network_items(snapshot, limit=2):
    """Select active physical-looking interfaces for dynamic LAN cards.

    Linux names are not guaranteed to be eth0/eth1 on every image.  Prefer
    common physical names, sort them once, and hide ports that have no carrier
    and no usable address. A bridge or loopback must not displace a physical
    port in the small-screen layout.
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
    visible = [item for item in selected if _network_item_visible(item)]
    return sorted(visible, key=lambda item: _safe_text(item.get("name")))[:limit]


def _network_ipv6(item, *, link_local):
    values = item.get("ipv6")
    if isinstance(values, (list, tuple)):
        for value in values[:8]:
            if not isinstance(value, str) or "%" in value:
                continue
            try:
                address = ipaddress.IPv6Address(value)
            except ValueError:
                continue
            if (not (address.is_loopback or address.is_unspecified or address.is_multicast)
                    and address.is_link_local == link_local):
                return str(address)
    return None


def _network_item_visible(item):
    carrier = item.get("carrier")
    if type(carrier) in (bool, int):
        if carrier == 1:
            return True
        if carrier == 0:
            return False
    return (_valid_ipv4(item.get("ipv4")) is not None
            or _network_ipv6(item, link_local=False) is not None)


def _network_port_label(item, fallback_index):
    """Keep the physical port number when filtering leaves only one card."""
    name = item.get("name")
    if isinstance(name, str):
        suffix = name[len(name.rstrip("0123456789")):]
        prefix = name[:-len(suffix)] if suffix else name
        if suffix and len(suffix) <= 3 and prefix in {"eth", "end", "lan", "wan"}:
            return "网口{}".format(int(suffix) + 1)
    return "网口{}".format(fallback_index + 1)


def _network_card_address(snapshot, item, index):
    """Return a card label/address, with an honest socket-free fallback once."""
    label = _safe_text(item.get("name"))
    address = _valid_ipv4(item.get("ipv4")) or _network_ipv6(item, link_local=False)
    if address is None and index == 0:
        fallback = _first_local_ipv4(snapshot)
        if fallback is not None:
            return "LOCAL IPv4", fallback
    return label, address or _network_ipv6(item, link_local=True) or "无IP"


def _network_ip6(item):
    """First valid IPv6 (global or link-local); a bare label when there is none."""
    return _network_ipv6(item, link_local=False) or _network_ipv6(item, link_local=True) or "无IPv6"


def _network_link(item, palette=None):
    p = palette or PALETTES["dark"]
    carrier = item.get("carrier")
    if type(carrier) in (bool, int) and carrier in (0, 1):
        return ("在线", p.good) if carrier else ("断开", p.muted)
    return ("--", p.muted)


def _fan_summary(fan):
    """Return a short kernel-controller summary; intentionally never RPM."""
    mode = "自动" if fan.get("mode") == "auto" or fan.get("control") == "kernel-thermal" else "--"
    pwm = fan.get("pwm_percent")
    if pwm is None and type(fan.get("pwm")) is int and 0 <= fan["pwm"] <= 255:
        pwm = (fan["pwm"] * 100 + 127) // 255
    if type(pwm) is int and 0 <= pwm <= 100:
        return "{} {}%".format(mode, pwm) if mode != "--" else "{}%".format(pwm)
    return mode


def _fan_present(fan):
    if not isinstance(fan, Mapping):
        return False
    return any(value != "--" for value in (
        _fan_mode(fan), _fan_level(fan), _pwm_percent_text(fan),
        _safe_text(fan.get("control")), _safe_text(fan.get("policy")),
    ))


def _storage_items(snapshot):
    return [item for item in _items(snapshot, "storage")
            if _finite_number(item.get("temp_mc"))]


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


@dataclass(frozen=True)
class Palette:
    """A colour skin. The eight pages share one layout; only colours change."""
    bg: str
    panel: str
    panel_alt: str
    fg: str
    muted: str
    accent: str
    accent2: str
    good: str
    warn: str
    line: str


PALETTES = {
    # Default deep-blue industrial skin with a cyan primary accent.
    "dark": Palette(bg="#08131d", panel="#102535", panel_alt="#0d1e2b", fg="#f3f7fb",
                    muted="#8ea3b7", accent="#2fe0cb", accent2="#9b8cff",
                    good="#65d48f", warn="#f2b665", line="#234052"),
    # Near-black neon skin with magenta/violet accents for a livelier look.
    "aurora": Palette(bg="#0a0812", panel="#1a1430", panel_alt="#140f24", fg="#f6f2ff",
                      muted="#9d8fc4", accent="#e0479e", accent2="#38e0ff",
                      good="#5ef2c0", warn="#ffb14e", line="#3a2c5c"),
    # Bright high-contrast skin for well-lit rooms; dark ink on light panels.
    "light": Palette(bg="#eef2f6", panel="#ffffff", panel_alt="#dde6ee", fg="#111c26",
                     muted="#5a6b7b", accent="#0f8f9c", accent2="#5a4fd0",
                     good="#1f9d57", warn="#c9791a", line="#b7c4d0"),
}

# Module-level defaults keep the shared helpers (`_text`, `_panel`, ...) working
# without a palette argument; they mirror the default "dark" skin.
_DARK = PALETTES["dark"]
BACKGROUND = _DARK.bg
PANEL = _DARK.panel
PANEL_ALT = _DARK.panel_alt
FOREGROUND = _DARK.fg
MUTED = _DARK.muted
ACCENT = _DARK.accent
GOOD = _DARK.good
WARNING = _DARK.warn
LINE = _DARK.line


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


def _column_boxes(count, top, bottom, *, left=10, right=418, gap=10):
    """Return stable full-width columns; missing cards consume no space."""
    if count <= 0:
        return []
    width = (right - left - gap * (count - 1)) // count
    boxes = []
    x = left
    for index in range(count):
        edge = right if index == count - 1 else x + width
        boxes.append((x, top, edge, bottom))
        x = edge + gap
    return boxes


def _empty_state(draw, text, palette, message):
    text(draw, (10, 66), message, 17, palette.muted, width=408, bold=True)


def _header(draw, text, palette, title, snapshot):
    """Shared top band: page title left, uptime right, divider line."""
    text(draw, (10, 8), title, 14, palette.accent, width=280, bold=True)
    text(draw, (323, 10), "运行 " + _uptime(snapshot.get("uptime_seconds")), 11,
         palette.muted, width=95)
    draw.line((10, 30, 418, 30), fill=palette.line)


def _bar(draw, box, fraction, palette, color):
    """Thin rounded progress bar; a None/invalid fraction draws an empty track."""
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=(bottom - top) // 2, fill=palette.panel_alt,
                           outline=palette.line, width=1)
    if _finite_number(fraction) and fraction > 0:
        span = right - left - 2
        filled = left + 1 + int(round(span * min(1.0, max(0.0, fraction))))
        if filled > left + 2:
            draw.rounded_rectangle((left + 1, top + 1, filled, bottom - 1),
                                   radius=(bottom - top) // 2 - 1, fill=color)


def _usage_fraction(snapshot):
    value = snapshot.get("cpu_usage_percent")
    return value / 100 if _nonnegative(value) and value <= 100 else None


def _usage_text(snapshot):
    value = snapshot.get("cpu_usage_percent")
    return "{:.0f}%".format(value) if _nonnegative(value) and value <= 100 else "--"


def _memory_fraction(snapshot):
    total, available = snapshot.get("mem_total_kib"), snapshot.get("mem_available_kib")
    if _nonnegative(total) and total > 0 and _nonnegative(available) and available <= total:
        return (total - available) / total
    return None


def _memory_text(snapshot):
    fraction = _memory_fraction(snapshot)
    return "{:.0f}%".format(fraction * 100) if fraction is not None else "--"


def _fan_level(fan):
    state, maximum = fan.get("state"), fan.get("max_state")
    if type(state) is int and type(maximum) is int and 0 <= state <= maximum <= 255:
        return "L{}/{}".format(state, maximum)
    return "--"


def _pwm_percent_text(fan):
    pwm = fan.get("pwm_percent")
    if pwm is None and type(fan.get("pwm")) is int and 0 <= fan["pwm"] <= 255:
        pwm = (fan["pwm"] * 100 + 127) // 255
    return "{}%".format(pwm) if type(pwm) is int and 0 <= pwm <= 100 else "--"


def _fan_mode(fan):
    return "自动" if fan.get("mode") == "auto" or fan.get("control") == "kernel-thermal" else "--"


def _fan_mapping(snapshot):
    fan = snapshot.get("fan")
    return fan if isinstance(fan, Mapping) else {}


def _khz_text(value):
    return "{:.0f} MHz".format(value / 1000) if type(value) is int and value > 0 else "--"


def _loadavg_values(snapshot):
    load = snapshot.get("loadavg")
    if isinstance(load, (list, tuple)) and len(load) >= 3:
        return ["{:.2f}".format(value) if _nonnegative(value) else "--" for value in load[:3]]
    return ["--", "--", "--"]


def _cpu_page_available(snapshot):
    frequency = snapshot.get("cpu_frequency")
    frequency = frequency if isinstance(frequency, Mapping) else {}
    return (_usage_fraction(snapshot) is not None
            or any(value != "--" for value in _loadavg_values(snapshot))
            or _khz_text(frequency.get("current_khz")) != "--"
            or _safe_text(frequency.get("governor")) != "--"
            or _safe_text(frequency.get("driver")) != "--")


def _memory_page_available(snapshot):
    return _memory_fraction(snapshot) is not None


def _thermal_page_available(snapshot):
    return (_finite_number(snapshot.get("cpu_temp_mc"))
            or _finite_number(snapshot.get("phy_temp_mc"))
            or _fan_present(snapshot.get("fan")))


def _page_overview(snapshot, palette, font_directory=None):
    """Primary page: active LAN cards and only available health metrics."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    fan = _fan_mapping(snapshot)
    ports = _network_items(snapshot)
    _header(draw, text, p, "E87N  /  系统", snapshot)
    text(draw, (10, 37), "设备状态", 10, p.muted, bold=True)
    carriers = [item.get("carrier") for item in ports]
    if any(type(value) in (bool, int) and value == 1 for value in carriers):
        health, health_color = "在线", p.good
    elif ports and all(type(value) in (bool, int) and value == 0 for value in carriers):
        health, health_color = "无链路", p.warn
    else:
        health, health_color = "未连接", p.muted
    draw.ellipse((104, 39, 111, 46), fill=health_color)
    text(draw, (116, 36), health, 12, health_color, bold=True)
    text(draw, (311, 36), "428 x 142", 10, p.muted, width=107)

    for index, (item, box) in enumerate(zip(ports, _column_boxes(len(ports), 52, 94))):
        left, top, right, bottom = box
        _panel(draw, box, fill=p.panel_alt,
               outline=p.accent if index == 0 else p.accent2)
        link, link_color = _network_link(item, p)
        name, address = _network_card_address(snapshot, item, index)
        text(draw, (left + 10, top + 6), _network_port_label(item, index), 11,
             p.accent, bold=True)
        draw.ellipse((right - 78, top + 7, right - 72, top + 13), fill=link_color)
        text(draw, (right - 66, top + 4), link, 11, link_color, width=56, bold=True)
        text(draw, (left + 10, top + 20), address, 16, p.fg,
             width=right - left - 20, bold=True)
        text(draw, (left + 10, bottom - 8), name, 10, p.muted, width=right - left - 20)

    metrics = []
    if _usage_fraction(snapshot) is not None:
        metrics.append(("CPU", _usage_text(snapshot), p.accent))
    if _memory_fraction(snapshot) is not None:
        metrics.append(("内存", _memory_text(snapshot), p.fg))
    if _finite_number(snapshot.get("cpu_temp_mc")):
        metrics.append(("温度", _temperature(snapshot.get("cpu_temp_mc")), p.warn))
    if _fan_present(fan):
        metrics.append(("风扇", _fan_summary(fan), p.good))
    for (label, value, color), box in zip(metrics, _column_boxes(len(metrics), 100, 137, gap=4)):
        left, top, right, _ = box
        _panel(draw, box, fill=p.panel, outline=color)
        text(draw, (left + 8, top + 2), label, 10, p.muted,
             width=right - left - 16, bold=True)
        text(draw, (left + 8, top + 17), value, 9 if label == "风扇" else 16, color,
             width=right - left - 16, bold=True)
    return image


def _page_cpu(snapshot, palette, font_directory=None):
    """CPU usage with a bar, load averages, and CPUFreq governor/driver facts."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    _header(draw, text, p, "CPU  /  负载", snapshot)
    usage = _usage_fraction(snapshot)
    load_values = _loadavg_values(snapshot)
    panels = []
    if usage is not None:
        panels.append("usage")
    if any(value != "--" for value in load_values):
        panels.append("load")
    for kind, box in zip(panels, _column_boxes(len(panels), 37, 95, gap=4)):
        left, top, right, bottom = box
        _panel(draw, box, fill=p.panel if kind == "usage" else p.panel_alt,
               outline=p.accent if kind == "usage" else p.accent2)
        if kind == "usage":
            text(draw, (left + 10, top + 6), "使用率", 10, p.muted, bold=True)
            text(draw, (left + 10, top + 19), _usage_text(snapshot), 26, p.accent,
                 width=right - left - 20, bold=True)
            _bar(draw, (left + 10, bottom - 11, right - 10, bottom - 5), usage, p, p.accent)
        else:
            text(draw, (left + 10, top + 6), "负载 1/5/15", 10, p.muted, bold=True)
            width = (right - left - 20) // 3
            for index, value in enumerate(load_values):
                text(draw, (left + 10 + index * width, top + 23), value, 15, p.fg,
                     width=width - 4, bold=True)
    freq = snapshot.get("cpu_frequency") if isinstance(snapshot.get("cpu_frequency"), Mapping) else {}
    details = []
    frequency = _khz_text(freq.get("current_khz"))
    governor = _safe_text(freq.get("governor"))
    driver = _safe_text(freq.get("driver"))
    if frequency != "--":
        details.append(("频率", frequency, p.warn))
    if governor != "--":
        details.append(("调度", governor, p.fg))
    if driver != "--":
        details.append(("驱动", driver, p.muted))
    for (label, value, color), box in zip(details, _column_boxes(len(details), 103, 137, gap=4)):
        left, top, right, _ = box
        _panel(draw, box, fill=p.panel_alt, outline=p.line)
        text(draw, (left + 8, top + 3), label, 9, p.muted, bold=True)
        text(draw, (left + 8, top + 16), value, 12, color,
             width=right - left - 16, bold=True)
    if not panels and not details:
        _empty_state(draw, text, p, "无 CPU 遥测")
    return image


def _page_memory(snapshot, palette, font_directory=None):
    """Memory used/available with a percentage bar; never invents swap or cache."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    _header(draw, text, p, "内存  /  占用", snapshot)
    total, available = snapshot.get("mem_total_kib"), snapshot.get("mem_available_kib")
    if _memory_fraction(snapshot) is None:
        _empty_state(draw, text, p, "无内存遥测")
        return image
    used = _bytes((total - available) * 1024)
    avail_text = _bytes(available * 1024)
    total_text = _bytes(total * 1024)
    _panel(draw, (10, 37, 418, 82), fill=p.panel, outline=p.accent)
    text(draw, (20, 43), "已用", 10, p.muted, bold=True)
    text(draw, (20, 56), _memory_text(snapshot), 21, p.accent, width=150, bold=True)
    text(draw, (200, 43), "已用 " + used, 12, p.fg, width=210, bold=True)
    text(draw, (200, 60), "可用 " + avail_text, 12, p.good, width=210, bold=True)
    _bar(draw, (10, 90, 418, 100), _memory_fraction(snapshot), p, p.accent)
    text(draw, (10, 110), "总量", 10, p.muted, bold=True)
    text(draw, (58, 107), total_text, 15, p.warn, width=160, bold=True)
    text(draw, (230, 110), "仅真实计量", 10, p.muted, width=188, bold=True)
    return image


def _page_thermal(snapshot, palette, font_directory=None):
    """CPU/PHY temperatures plus the kernel fan controller facts (never RPM)."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    fan = _fan_mapping(snapshot)
    _header(draw, text, p, "温度  /  风扇", snapshot)
    cards = []
    if _finite_number(snapshot.get("cpu_temp_mc")):
        cards.append(("temp", "CPU", snapshot.get("cpu_temp_mc"), p.warn))
    if _finite_number(snapshot.get("phy_temp_mc")):
        cards.append(("temp", "PHY", snapshot.get("phy_temp_mc"), p.accent))
    if _fan_present(fan):
        cards.append(("fan", "风扇", fan, p.good))
    for card, box in zip(cards, _column_boxes(len(cards), 43, 104, gap=8)):
        kind, label, value, color = card
        left, top, right, _ = box
        _panel(draw, box, fill=p.panel if kind == "temp" else p.panel_alt, outline=color)
        if kind == "temp":
            text(draw, (left + 10, top + 7), label, 11, p.muted, bold=True)
            text(draw, (left + 10, top + 25), _temperature(value), 21, color,
                 width=right - left - 20, bold=True)
        else:
            facts = [("模式", _fan_mode(value), p.good),
                     ("档位", _fan_level(value), p.fg),
                     ("PWM", _pwm_percent_text(value), p.accent)]
            y = top + 6
            for fact_label, fact_value, fact_color in facts:
                if fact_value == "--":
                    continue
                text(draw, (left + 10, y), fact_label, 9, p.muted, bold=True)
                text(draw, (left + 52, y - 1), fact_value, 11, fact_color,
                     width=right - left - 62, bold=True)
                y += 17
    policy = _safe_text(fan.get("policy"))
    if policy != "--":
        text(draw, (10, 116), "内核策略", 10, p.muted, bold=True)
        text(draw, (78, 113), policy, 13, p.fg, width=170)
        text(draw, (300, 115), "无测速", 10, p.muted, width=118, bold=True)
    if not cards:
        _empty_state(draw, text, p, "无温度或风扇遥测")
    return image


def _page_fan(snapshot, palette, font_directory=None):
    """Kernel fan controller facts: mode, cooling level, PWM, policy (never RPM)."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    fan = _fan_mapping(snapshot)
    _header(draw, text, p, "风扇  /  内核", snapshot)
    if not _fan_present(fan):
        _empty_state(draw, text, p, "无风扇遥测")
        return image
    facts = []
    for label, value, color in (
        ("模式", _fan_mode(fan), p.good),
        ("档位", _fan_level(fan), p.fg),
        ("PWM", _pwm_percent_text(fan), p.accent),
    ):
        if value != "--":
            facts.append((label, value, color))
    for (label, value, color), box in zip(facts, _column_boxes(len(facts), 37, 101, gap=6)):
        left, top, right, bottom = box
        _panel(draw, box, fill=p.panel_alt, outline=color)
        text(draw, (left + 8, top + 7), label, 9, p.muted, bold=True)
        text(draw, (left + 8, top + 23), value, 16 if len(facts) > 2 else 22, color,
             width=right - left - 16, bold=True)
        if label == "PWM":
            stripped = value.rstrip("%")
            fraction = int(stripped) / 100 if stripped.isdigit() else None
            _bar(draw, (left + 8, bottom - 11, right - 8, bottom - 5), fraction, p, p.accent)
    policy = _safe_text(fan.get("policy"))
    if policy != "--":
        text(draw, (10, 115), "策略", 9, p.muted, bold=True)
        text(draw, (44, 113), policy, 10, p.fg, width=160, bold=True)
    text(draw, (350, 114), "无测速", 9, p.muted, width=68, bold=True)
    return image


def _page_network(snapshot, palette, font_directory=None):
    """Active physical ports with link, addresses and cumulative counters."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    _header(draw, text, p, "网络  /  网口", snapshot)
    ports = _network_items(snapshot)
    if not ports:
        _empty_state(draw, text, p, "无活动网口")
        return image
    for index, (item, box) in enumerate(zip(ports, _column_boxes(len(ports), 37, 137))):
        left, top, right, _ = box
        _panel(draw, box, fill=p.panel_alt,
               outline=p.accent if index == 0 else p.accent2)
        link, link_color = _network_link(item, p)
        name = _safe_text(item.get("name"))
        ipv4 = _valid_ipv4(item.get("ipv4"))
        if ipv4 is None and index == 0:
            fallback = _first_local_ipv4(snapshot)
            if fallback is not None:
                name, ipv4 = "LOCAL IPv4", fallback
        text(draw, (left + 10, top + 6), _network_port_label(item, index), 11,
             p.accent, bold=True)
        draw.ellipse((right - 78, top + 8, right - 72, top + 14), fill=link_color)
        text(draw, (right - 66, top + 5), link, 11, link_color, width=56, bold=True)
        text(draw, (left + 10, top + 22), name, 10, p.muted, width=right - left - 20)
        text(draw, (left + 10, top + 38), ipv4 or "无IPv4", 15, p.fg,
             width=right - left - 20, bold=True)
        counter_width = (right - left - 20) // 2
        text(draw, (left + 10, top + 61), "RX " + _bytes(item.get("rx_bytes")),
             10, p.good, width=counter_width)
        text(draw, (left + 10 + counter_width, top + 61),
             "TX " + _bytes(item.get("tx_bytes")), 10, p.warn, width=counter_width)
        ipv6 = _network_ip6(item)
        if ipv6 != "无IPv6":
            text(draw, (left + 10, top + 81), ipv6, 10, p.muted,
                 width=right - left - 20)
    return image


def _page_traffic(snapshot, palette, font_directory=None):
    """Aggregate RX/TX totals across ports plus the primary link/local IPv4."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    _header(draw, text, p, "流量  /  累计", snapshot)
    ports = _network_items(snapshot)
    if not ports:
        _empty_state(draw, text, p, "无活动网口")
        return image
    rx = sum(item.get("rx_bytes") for item in ports
             if _nonnegative(item.get("rx_bytes"))) if ports else None
    tx = sum(item.get("tx_bytes") for item in ports
             if _nonnegative(item.get("tx_bytes"))) if ports else None
    any_rx = any(_nonnegative(item.get("rx_bytes")) for item in ports)
    any_tx = any(_nonnegative(item.get("tx_bytes")) for item in ports)
    totals = []
    if any_rx:
        totals.append(("RX 累计", _bytes(rx), p.good))
    if any_tx:
        totals.append(("TX 累计", _bytes(tx), p.warn))
    for (label, value, color), box in zip(totals, _column_boxes(len(totals), 37, 112, gap=8)):
        left, top, right, _ = box
        _panel(draw, box, fill=p.panel, outline=color)
        text(draw, (left + 10, top + 9), label, 10, p.muted, bold=True)
        text(draw, (left + 10, top + 29), value, 20, color,
             width=right - left - 20, bold=True)
    main = next((item for item in ports
                 if type(item.get("carrier")) in (bool, int) and item.get("carrier") == 1),
                ports[0])
    link, link_color = _network_link(main, p)
    address_label, address = _network_card_address(snapshot, main, 0)
    text(draw, (10, 122), "链路", 9, p.muted, bold=True)
    text(draw, (46, 122), link, 10, link_color, width=54, bold=True)
    text(draw, (110, 122), address_label, 9, p.muted, width=84, bold=True)
    text(draw, (194, 122), address, 10, p.fg, width=224)
    return image


def _page_storage(snapshot, palette, font_directory=None):
    """NVMe device composite temperatures; the page is skipped when the list is empty."""
    p = palette
    image = Image.new("RGB", (WIDTH, HEIGHT), p.bg)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    _header(draw, text, p, "存储  /  温度", snapshot)
    devices = _storage_items(snapshot)
    if not devices:
        _empty_state(draw, text, p, "无存储温度遥测")
        return image
    for index, item in enumerate(devices[:3]):
        y = 40 + index * 33
        _panel(draw, (10, y, 418, y + 29), fill=p.panel_alt, outline=p.accent)
        text(draw, (20, y + 7), _safe_text(item.get("name")), 13, p.fg, width=260, bold=True)
        text(draw, (320, y + 6), _temperature(item.get("temp_mc")), 15, p.warn,
             width=90, bold=True)
    return image


_PAGES = {
    "overview": _page_overview, "cpu": _page_cpu, "memory": _page_memory,
    "thermal": _page_thermal, "fan": _page_fan, "network": _page_network,
    "traffic": _page_traffic, "storage": _page_storage,
}


def render(snapshot, screen="overview", *, theme="dark", font_directory=None):
    """Render one 428x142 page; the theme selects a colour skin, not a layout."""
    if not isinstance(snapshot, Mapping):
        raise DisplayError("snapshot must be a mapping")
    if theme not in THEMES:
        raise DisplayError("unknown theme: " + str(theme))
    if screen not in _PAGES:
        raise DisplayError("unknown screen: " + str(screen))
    return _PAGES[screen](snapshot, PALETTES[theme], font_directory=font_directory)


def _available_pages(snapshot, pages):
    """Drop pages whose complete data group is absent from a live snapshot.

    Overview is always useful. Automatic rotation only visits pages with real
    content, and a fixed unavailable page falls back to overview until its
    telemetry returns. If every requested page is unavailable, use overview so
    the daemon never rotates over an empty set.
    """
    network = _network_items(snapshot)
    network_present = bool(network)
    traffic_present = any(
        _nonnegative(item.get(key))
        for item in network
        for key in ("rx_bytes", "tx_bytes")
    )
    availability = {
        "overview": True,
        "cpu": _cpu_page_available(snapshot),
        "memory": _memory_page_available(snapshot),
        "thermal": _thermal_page_available(snapshot),
        "fan": _fan_present(snapshot.get("fan")),
        "network": network_present,
        "traffic": traffic_present,
        "storage": bool(_storage_items(snapshot)),
    }
    available = [page for page in pages if availability.get(page, False)]
    return available or ["overview"]


def preview_snapshot():
    """Deterministic sample data, explicitly not measurements of a live board."""
    return {
        "cpu_usage_percent": 24.0, "cpu_temp_mc": 58750, "phy_temp_mc": 43250,
        "fan": {"state": 2, "max_state": 3, "pwm": 192, "rpm": None, "policy": "step_wise",
                "control": "kernel-thermal", "mode": "auto"},
        "cpu_frequency": {"current_khz": 1800000, "governor": "ondemand",
                          "driver": "cpufreq-dt", "available_khz": "408000 1800000"},
        "loadavg": [0.42, 0.31, 0.28], "mem_total_kib": 1048576,
        "mem_available_kib": 655360, "uptime_seconds": 183845.0,
        "local_ipv4": ["192.0.2.87"],
        "network": [
            {"name": "end0", "ipv4": "192.0.2.87", "ipv6": ["2001:db8::87"],
             "rx_bytes": 34123456789, "tx_bytes": 8123456789, "carrier": True},
            {"name": "eth1", "ipv4": "198.51.100.23", "ipv6": [],
             "rx_bytes": 1234567890, "tx_bytes": 345678901, "carrier": True},
            {"name": "br-lan", "rx_bytes": None, "tx_bytes": None, "carrier": None},
        ],
        "storage": [{"name": "nvme0", "temp_mc": 41250}, {"name": "nvme1", "temp_mc": None}],
    }


def read_snapshot():
    # Preview never imports hardware; disabled iterations only load its config.
    from . import hardware as hw
    return hw.snapshot()


def _daemon_pages(config, snapshot):
    """Return live pages for rotation or the fixed-page fallback."""
    pages = config["rotation_screens"] if config["rotation_enabled"] else [config["screen"]]
    return _available_pages(snapshot, pages) if snapshot is not None else pages


def run_daemon():
    framebuffer = None
    active_screen = None
    config_identity = None
    rotation_deadline = 0.0
    try:
        while True:
            config = load_display_config()
            # Read once per enabled loop, after the device is open so a snapshot
            # failure still closes the framebuffer; disabled loops never sample.
            snapshot = None
            if config["enabled"]:
                if framebuffer is None:
                    framebuffer = Framebuffer()
                snapshot = read_snapshot()
            identity = (config["screen"], config["theme"], config["rotation_enabled"],
                        config["rotation_seconds"], tuple(config["rotation_screens"]))
            now = time.monotonic()
            pages = _daemon_pages(config, snapshot)
            if identity != config_identity:
                active_screen = (config["screen"] if config["rotation_enabled"]
                                 and config["screen"] in pages else pages[0])
                rotation_deadline = now + config["rotation_seconds"]
                config_identity = identity
            elif config["rotation_enabled"]:
                if active_screen not in pages:
                    active_screen = pages[0]
                    rotation_deadline = now + config["rotation_seconds"]
                elif now >= rotation_deadline:
                    active_screen = pages[(pages.index(active_screen) + 1) % len(pages)]
                    rotation_deadline = now + config["rotation_seconds"]
            elif not config["rotation_enabled"]:
                active_screen = pages[0]
            if config["enabled"]:
                framebuffer.draw(render(snapshot, active_screen, theme=config["theme"]))
            sleep_for = config["refresh_seconds"]
            if config["rotation_enabled"]:
                sleep_for = min(sleep_for, max(0.1, rotation_deadline - time.monotonic()))
            time.sleep(sleep_for)
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
    parser.add_argument("--theme", choices=THEMES, help="preview colour theme (default: dark)")
    parser.add_argument("--preview-font-dir", "--font-dir", type=Path, metavar="DIRECTORY",
                        help="preview-only directory containing DejaVu Sans regular/bold fonts")
    args = parser.parse_args(argv)
    if args.daemon and (args.screen is not None or args.theme is not None or args.preview_font_dir is not None):
        parser.error("--screen, --theme and --preview-font-dir are for --preview; the daemon uses display.json and Debian fonts")
    try:
        if args.preview is not None:
            output = _offline_path(args.preview)
            font_directory = _offline_path(args.preview_font_dir) if args.preview_font_dir is not None else None
            image = render(preview_snapshot(), args.screen or "overview", theme=args.theme or "dark",
                           font_directory=font_directory)
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
