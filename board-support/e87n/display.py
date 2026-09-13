"""Debian-native, read-only telemetry renderer for the E87N NV3007 fbdev.

Runtime dependencies: Python 3, python3-pil, fonts-dejavu-core. Install this
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
The hardware/control service owns brightness and fan policy: this module
NEVER writes backlight, fan or sysfs files.
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
    if var.bits_per_pixel != 16 or var.grayscale or var.nonstd or var.vmode:
        raise DisplayError("framebuffer must be standard progressive 16-bit RGB")
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


@lru_cache(maxsize=32)
def _font(size, bold=False, font_directory=None):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str((font_directory or FONT_DIRECTORY) / name), size)


BACKGROUND = "#101923"
FOREGROUND = "#f3f7fb"
MUTED = "#acbaca"
ACCENT = "#69ddce"


def _text(draw, xy, text, size=16, color=FOREGROUND, width=None, bold=False, font_directory=None):
    font = _font(size, bold, font_directory)
    text = str(text)
    if width is not None and draw.textbbox((0, 0), text, font=font)[2] > width:
        while text and draw.textbbox((0, 0), text + "...", font=font)[2] > width:
            text = text[:-1]
        text += "..."
    draw.text(xy, text, font=font, fill=color, anchor="lt")


def render(snapshot, screen="overview", *, font_directory=None):
    """Pure 428x142 RGB renderer: snapshot data + packaged fonts only.

    Temperatures are millidegrees C; memory KiB; network counters cumulative
    bytes, NOT rates. Fan state, raw PWM (0..255) and actual RPM stay distinct.
    Missing/invalid readings render as '--', including absent tachometer RPM.
    """
    if not isinstance(snapshot, Mapping):
        raise DisplayError("hardware.snapshot() must return a mapping")
    if screen not in SCREENS:
        raise DisplayError("unknown screen: " + str(screen))
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    text = partial(_text, font_directory=font_directory)
    titles = {"overview": "SYSTEM OVERVIEW", "thermal": "THERMAL",
              "network": "NETWORK TOTALS", "storage": "STORAGE TEMPERATURE"}
    text(draw, (10, 7), titles[screen], 15, ACCENT, width=315, bold=True)
    items = _items(snapshot, screen) if screen in ("network", "storage") else []
    tag = "+{} more".format(len(items) - 3) if len(items) > 3 else "E87N"
    text(draw, (340, 8), tag, 12, MUTED, width=78)
    draw.line((10, 28, 417, 28), fill="#344354")
    fan = snapshot.get("fan")
    fan = fan if isinstance(fan, Mapping) else {}

    if screen == "overview":
        total, available = snapshot.get("mem_total_kib"), snapshot.get("mem_available_kib")
        memory = "--"
        if _nonnegative(total) and total > 0 and _nonnegative(available) and available <= total:
            memory = "{:.0f}%".format((total - available) * 100 / total)
        for x, label, value in (
            (10, "CPU", _temperature(snapshot.get("cpu_temp_mc"))),
            (152, "PHY", _temperature(snapshot.get("phy_temp_mc"))),
            (294, "MEM USED", memory),
        ):
            text(draw, (x, 38), label, 13, MUTED)
            text(draw, (x, 57), value, 25, width=125, bold=True)
        load = snapshot.get("loadavg")
        load = load[0] if isinstance(load, (list, tuple)) and load else None
        for x, label, value in (
            (10, "LOAD 1m", "{:.2f}".format(load) if _nonnegative(load) else "--"),
            (152, "FAN RPM", _integer(fan.get("rpm"))),
            (294, "UPTIME", _uptime(snapshot.get("uptime_seconds"))),
        ):
            text(draw, (x, 97), label, 12, MUTED)
            text(draw, (x, 116), value, 17, width=124)
    elif screen == "thermal":
        for x, label, value in (
            (10, "CPU", snapshot.get("cpu_temp_mc")),
            (145, "PHY", snapshot.get("phy_temp_mc")),
        ):
            text(draw, (x, 39), label, 13, MUTED)
            text(draw, (x, 61), _temperature(value), 25, width=128, bold=True)
        state = _integer(fan.get("state")) + "/" + _integer(fan.get("max_state"))
        pwm = fan.get("pwm")
        pwm = _integer(pwm) if type(pwm) is int and 0 <= pwm <= 255 else "--"
        for y, label, value in ((39, "STATE", state), (64, "PWM", pwm + "/255"),
                                (89, "RPM", _integer(fan.get("rpm")))):
            text(draw, (280, y), label, 12, MUTED)
            text(draw, (332, y), value, 14, width=86)
        text(draw, (10, 119), "POLICY  " + _safe_text(fan.get("policy")), 14, MUTED, width=408)
    elif screen == "network":
        for x, label in ((10, "IFACE"), (121, "LINK"), (190, "RX TOTAL"), (308, "TX TOTAL")):
            text(draw, (x, 37), label, 12, MUTED)
        for index, item in enumerate((items or [{}])[:3]):
            y = 58 + index * 28
            carrier = item.get("carrier")
            link = "--"
            if type(carrier) in (bool, int) and carrier in (0, 1):
                link = "UP" if carrier else "DOWN"
            for x, value, width in (
                (10, _safe_text(item.get("name")), 102), (121, link, 61),
                (190, _bytes(item.get("rx_bytes")), 110),
                (308, _bytes(item.get("tx_bytes")), 110),
            ):
                text(draw, (x, y), value, 15, width=width)
    else:
        for index, item in enumerate((items or [{}])[:3]):
            y = 39 + index * 33
            text(draw, (10, y + 3), _safe_text(item.get("name")), 17, width=234)
            text(draw, (258, y), _temperature(item.get("temp_mc")), 25, width=160, bold=True)
    return image


def preview_snapshot():
    """Deterministic sample data, explicitly not measurements of a live board."""
    return {
        "cpu_temp_mc": 58750, "phy_temp_mc": 43250,
        "fan": {"state": 2, "max_state": 3, "pwm": 192, "rpm": None, "policy": "kernel"},
        "loadavg": [0.42, 0.31, 0.28], "mem_total_kib": 1048576,
        "mem_available_kib": 655360, "uptime_seconds": 183845.0,
        "network": [
            {"name": "eth0", "rx_bytes": 34123456789, "tx_bytes": 8123456789, "carrier": True},
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
            info = candidate.lstat()
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
