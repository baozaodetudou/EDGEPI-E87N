#!/usr/bin/env python3
"""Fixture-only display tests; never open a real framebuffer, /sys or /proc.

    python3 -B tests/test-display.py

Requires Pillow and DejaVu Sans (Debian python3-pil/fonts-dejavu-core).
For a non-Debian host, use the parent's output/runtime/display-fonts fixture,
or set E87N_TEST_FONT_DIR to an existing directory containing DejaVuSans.ttf
and DejaVuSans-Bold.ttf. No assets are downloaded.
These are userspace ABI/renderer tests, NOT hardware or kernel validation.
"""

from contextlib import ExitStack
import ctypes
import errno
import io
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "board-support"))
import e87n
from e87n import display
from PIL import Image, ImageDraw


def fixture_config(enabled=True, brightness=20, screen="overview", refresh=2, theme="dark",
                   rotation_enabled=False, rotation_seconds=3, rotation_screens=None):
    if rotation_screens is None:
        rotation_screens = ["overview", "cpu", "memory", "thermal", "fan", "network", "traffic", "storage"]
    return {"enabled": enabled, "brightness_percent": brightness,
            "screen": screen, "refresh_seconds": refresh, "theme": theme,
            "rotation_enabled": rotation_enabled, "rotation_seconds": rotation_seconds,
            "rotation_screens": rotation_screens}


def fixture_font_directory():
    explicit = os.environ.get("E87N_TEST_FONT_DIR")
    if explicit:
        return Path(explicit)
    if (display.FONT_DIRECTORY / "DejaVuSans.ttf").is_file():
        return display.FONT_DIRECTORY
    return Path(__file__).resolve().parents[1] / "output/runtime/display-fonts"


def guard_hardware_paths(function):
    def guarded(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            candidate = os.fsdecode(path)
            if any(candidate == root or candidate.startswith(root + "/") for root in ("/dev", "/sys", "/proc")):
                raise AssertionError("fixture touched " + candidate)
        return function(path, *args, **kwargs)
    return guarded


def screen_info(*, padding=0, xoffset=0, yoffset=0):
    fix, var = display.FbFixScreeninfo(), display.FbVarScreeninfo()
    fix.id = b"fb_nv3007"
    fix.type, fix.visual = 0, 2
    var.xres, var.yres = display.WIDTH, display.HEIGHT
    var.xres_virtual = display.WIDTH + xoffset
    var.yres_virtual = display.HEIGHT + yoffset
    var.xoffset, var.yoffset = xoffset, yoffset
    var.bits_per_pixel = 16
    var.red = display.FbBitfield(11, 5, 0)
    var.green = display.FbBitfield(5, 6, 0)
    var.blue = display.FbBitfield(0, 5, 0)
    fix.line_length = var.xres_virtual * 2 + padding
    fix.smem_len = fix.line_length * var.yres_virtual
    return fix, var


def device_info(*, mode=stat.S_IFCHR, major=29, inode=101):
    return SimpleNamespace(st_mode=mode | 0o600, st_rdev=os.makedev(major, 0), st_dev=42, st_ino=inode)


class FakeDevice:
    """An in-memory fd/ioctl/write fixture; every device operation is patched."""

    def __init__(self, fix=None, var=None):
        self.fix, self.var = (fix, var) if fix is not None else screen_info()
        self.memory = bytearray(b"\xa5" * self.fix.smem_len)
        self.calls = []

    def ioctl(self, fd, request, buffer, mutate):
        assert fd == 87 and mutate is True
        self.calls.append(request)
        structure = {display.FBIOGET_FSCREENINFO: self.fix, display.FBIOGET_VSCREENINFO: self.var}[request]
        assert len(buffer) == ctypes.sizeof(structure)
        buffer[:] = bytes(structure)
        return 0

    def write(self, fd, data, offset):
        assert fd == 87
        if offset < 0 or offset + len(data) > len(self.memory):
            raise AssertionError("write outside fixture framebuffer")
        self.memory[offset:offset + len(data)] = data
        return len(data)

    def __enter__(self):
        self.stack = ExitStack()
        patch = self.stack.enter_context
        self.compatible = patch(mock.patch.object(Path, "read_bytes", return_value=b"edgepi,e87n\0mediatek,mt7987a\0"))
        self.stat = patch(mock.patch.object(display.os, "stat", return_value=device_info()))
        self.fstat = patch(mock.patch.object(display.os, "fstat", return_value=device_info()))
        self.open = patch(mock.patch.object(display.os, "open", return_value=87))
        self.close = patch(mock.patch.object(display.os, "close"))
        self.ioctl_mock = patch(mock.patch.object(display.fcntl, "ioctl", side_effect=self.ioctl))
        self.pwrite = patch(mock.patch.object(display.os, "pwrite", side_effect=self.write))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


class AbiTests(unittest.TestCase):
    def test_native_linux_struct_sizes_and_offsets(self):
        bits = ctypes.sizeof(ctypes.c_ulong) * 8
        self.assertIn(bits, (32, 64))
        self.assertEqual(ctypes.sizeof(display.FbBitfield), 12)
        self.assertEqual(ctypes.sizeof(display.FbVarScreeninfo), 160)
        self.assertEqual(ctypes.sizeof(display.FbFixScreeninfo), 80 if bits == 64 else 68)
        offsets = ({"smem_start": 16, "smem_len": 24, "line_length": 48,
                    "mmio_start": 56, "capabilities": 72, "reserved": 74} if bits == 64 else
                   {"smem_start": 16, "smem_len": 20, "line_length": 44,
                    "mmio_start": 48, "capabilities": 64, "reserved": 66})
        for field, offset in offsets.items():
            self.assertEqual(getattr(display.FbFixScreeninfo, field).offset, offset, field)
        for field, offset in {"red": 32, "green": 44, "blue": 56, "transp": 68,
                              "nonstd": 80, "vmode": 132, "rotate": 136,
                              "colorspace": 140, "reserved": 144}.items():
            self.assertEqual(getattr(display.FbVarScreeninfo, field).offset, offset, field)

    def test_fixed_info_32_and_64_bit_layout_models(self):
        # Independent ILP32/LP64 models exercise pointer-width field substitution
        # without loading an arm64 library or accessing a VM/device.
        for ulong, expected_size, line_offset in ((ctypes.c_uint32, 68, 44), (ctypes.c_uint64, 80, 48)):
            class Model(ctypes.Structure):
                _fields_ = [(name, ulong if name in ("smem_start", "mmio_start") else kind)
                            for name, kind in display.FbFixScreeninfo._fields_]
            self.assertEqual(ctypes.sizeof(Model), expected_size)
            self.assertEqual(Model.line_length.offset, line_offset)

    def test_native_ulong_preserves_high_address_bits(self):
        fix, _ = screen_info()
        address = 0x123456789ABCDEF0 if ctypes.sizeof(ctypes.c_ulong) == 8 else 0x89ABCDEF
        fix.smem_start = address
        copy = display.FbFixScreeninfo.from_buffer_copy(bytes(fix))
        self.assertEqual(copy.smem_start, address)
        self.assertEqual(copy.line_length, 856)

    def test_var_decodes_independent_kernel_byte_fixture(self):
        payload = bytearray(160)
        prefix = "<" if sys.byteorder == "little" else ">"
        for offset, value in {0: 428, 4: 142, 8: 432, 12: 144, 16: 4, 20: 2,
                              24: 16, 32: 11, 36: 5, 44: 5, 48: 6, 60: 5, 136: 270}.items():
            struct.pack_into(prefix + "I", payload, offset, value)
        var = display.FbVarScreeninfo.from_buffer_copy(payload)
        fix, _ = screen_info(xoffset=4, yoffset=2)
        layout = display.validate_framebuffer(fix, var)
        self.assertEqual(layout.start, 2 * 864 + 8)
        self.assertEqual(var.rotate, 270)


class ValidationTests(unittest.TestCase):
    def test_valid_geometry_padding_and_offsets(self):
        fix, var = screen_info(padding=12, xoffset=4, yoffset=2)
        layout = display.validate_framebuffer(fix, var)
        self.assertEqual(layout.stride, 876)
        self.assertEqual(layout.start, 1760)
        self.assertEqual(layout.memory_bytes, 876 * 144)

    def test_e87n_fbtft_nonstd_marker_is_accepted(self):
        fix, var = screen_info()
        var.nonstd = 1
        self.assertEqual(display.validate_framebuffer(fix, var).stride, 856)

    def test_reject_wrong_id_geometry_format_memory_and_overflow(self):
        cases = [
            ("fix", "id", b"simpledrm"), ("fix", "id", b"fb_nv3007-other"),
            ("var", "xres", 142), ("var", "yres", 428),
            ("var", "bits_per_pixel", 32), ("var", "grayscale", 1),
            ("var", "nonstd", 2), ("var", "vmode", 256),
            ("fix", "type", 1), ("fix", "type_aux", 1), ("fix", "visual", 3),
            ("var", "xres_virtual", 427), ("var", "yres_virtual", 141),
            ("var", "xoffset", 1), ("var", "yoffset", 1),
            ("fix", "line_length", 855), ("fix", "line_length", 854),
            ("fix", "line_length", 0), ("fix", "smem_len", 0),
            ("fix", "smem_len", 856 * 142 - 1),
            ("fix", "smem_len", display.MAX_FB_BYTES + 1),
            ("fix", "line_length", 0xFFFFFFFE),
            ("var", "xoffset", 0xFFFFFFFF), ("var", "yoffset", 0xFFFFFFFF),
            ("var", "xres_virtual", 0xFFFFFFFF), ("var", "yres_virtual", 0xFFFFFFFF),
        ]
        for target, field, value in cases:
            with self.subTest(target=target, field=field, value=value):
                fix, var = screen_info()
                setattr(fix if target == "fix" else var, field, value)
                with self.assertRaises(display.DisplayError):
                    display.validate_framebuffer(fix, var)

    def test_accepts_fbtft_fb_nonstd_ham_marker_with_rgb565_layout(self):
        fix, var = screen_info()
        var.nonstd = 1
        layout = display.validate_framebuffer(fix, var)
        self.assertEqual((layout.red_offset, layout.green_offset, layout.blue_offset), (11, 5, 0))

    def test_reject_bad_or_overlapping_bitfields(self):
        for name, member, value in (
            ("red", "length", 6), ("green", "length", 5), ("blue", "length", 0),
            ("red", "offset", 12), ("blue", "offset", 11),
            ("green", "offset", 0xFFFFFFFF), ("red", "msb_right", 1),
            ("transp", "length", 1), ("transp", "msb_right", 1), ("transp", "offset", 17),
        ):
            with self.subTest(name=name, member=member, value=value):
                fix, var = screen_info()
                setattr(getattr(var, name), member, value)
                with self.assertRaises(display.DisplayError):
                    display.validate_framebuffer(fix, var)

    def test_pack_colors_both_byte_orders_and_bgr_bitfields(self):
        fix, var = screen_info()
        for bgr in (False, True):
            if bgr:
                var.red.offset, var.blue.offset = 0, 11
            layout = display.validate_framebuffer(fix, var)
            for order in ("little", "big"):
                for rgb, word in (((255, 0, 0), 0x001F if bgr else 0xF800),
                                  ((0, 255, 0), 0x07E0),
                                  ((0, 0, 255), 0xF800 if bgr else 0x001F),
                                  ((255, 255, 255), 0xFFFF), ((0, 0, 0), 0)):
                    with self.subTest(bgr=bgr, order=order, rgb=rgb):
                        packed = display.pack_rgb565(Image.new("RGB", (428, 142), rgb), layout, order)
                        self.assertEqual(packed, word.to_bytes(2, order) * (428 * 142))

    def test_quantization_and_pixel_order(self):
        layout = display.validate_framebuffer(*screen_info())
        image = Image.new("RGB", (428, 142))
        image.putpixel((0, 0), (128, 64, 32))
        image.putpixel((427, 0), (255, 255, 255))
        image.putpixel((0, 1), (0, 255, 0))
        data = display.pack_rgb565(image, layout, "little")
        self.assertEqual(data[:2], b"\x04\x82")
        self.assertEqual(data[854:858], b"\xff\xff\xe0\x07")

    def test_pack_rejects_wrong_image_size_and_endianness(self):
        layout = display.validate_framebuffer(*screen_info())
        with self.assertRaises(display.DisplayError):
            display.pack_rgb565(Image.new("RGB", (142, 428)), layout)
        with self.assertRaises(ValueError):
            display.pack_rgb565(Image.new("RGB", (428, 142)), layout, "middle")


class DeviceTests(unittest.TestCase):
    def test_compatible_exact_token_and_fail_before_device_open(self):
        for compatible in (b"edgepi,e87n-pro\0", b"other,board\0", b"edgepi,e87n,mediatek", b"edgepi,e87n"):
            with self.subTest(compatible=compatible), FakeDevice() as device:
                device.compatible.return_value = compatible
                with self.assertRaises(display.DisplayError):
                    display.Framebuffer()
                device.open.assert_not_called()
                device.stat.assert_not_called()

    def test_regular_symlink_block_and_wrong_major_rejected_before_open(self):
        for mode, major in ((stat.S_IFREG, 29), (stat.S_IFLNK, 29), (stat.S_IFBLK, 29),
                            (stat.S_IFIFO, 29), (stat.S_IFCHR, 1)):
            with self.subTest(mode=mode, major=major), FakeDevice() as device:
                device.stat.return_value = device_info(mode=mode, major=major)
                with self.assertRaises(display.DisplayError):
                    display.Framebuffer()
                device.open.assert_not_called()

    def test_replaced_device_is_rejected_and_closed(self):
        for info in (device_info(major=1), device_info(inode=102)):
            with self.subTest(info=info), FakeDevice() as device:
                device.fstat.return_value = info
                with self.assertRaises(display.DisplayError):
                    display.Framebuffer()
                device.close.assert_called_once_with(87)
                device.ioctl_mock.assert_not_called()

    def test_invalid_ioctl_metadata_closes_without_writing(self):
        with FakeDevice() as device:
            device.fix.id = b"wrong"
            with self.assertRaises(display.DisplayError):
                display.Framebuffer()
            device.close.assert_called_once_with(87)
            device.pwrite.assert_not_called()

    def test_ioctl_failure_closes_and_propagates(self):
        with FakeDevice() as device:
            device.ioctl_mock.side_effect = OSError(errno.ENOTTY, "fixture ioctl")
            with self.assertRaises(OSError):
                display.Framebuffer()
            device.close.assert_called_once_with(87)

    def test_contiguous_frame_and_ioctl_requests(self):
        with FakeDevice() as device:
            fb = display.Framebuffer()
            fb.draw(Image.new("RGB", (428, 142), "white"))
            self.assertEqual(device.memory, b"\xff" * (428 * 142 * 2))
            self.assertEqual(device.calls, [0x4602, 0x4600, 0x4602, 0x4600])
            device.pwrite.assert_called_once()
            flags = device.open.call_args.args[1]
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_NONBLOCK)
            fb.close()
            fb.close()
            device.close.assert_called_once_with(87)
            with self.assertRaises(display.DisplayError):
                fb.draw(Image.new("RGB", (428, 142)))

    def test_stride_offsets_preserve_padding_and_hidden_pixels(self):
        fix, var = screen_info(padding=12, xoffset=4, yoffset=2)
        with FakeDevice(fix, var) as device:
            fb = display.Framebuffer()
            image = Image.new("RGB", (428, 142), "red")
            fb.draw(image)
            expected = bytearray(b"\xa5" * fix.smem_len)
            red = (0xF800).to_bytes(2, sys.byteorder) * 428
            for row in range(142):
                start = (row + 2) * 876 + 8
                expected[start:start + 856] = red
            self.assertEqual(device.memory, expected)
            self.assertEqual(device.pwrite.call_count, 142)
            fb.close()

    def test_short_writes_and_interrupted_write(self):
        with FakeDevice() as device:
            count = 0
            def short_write(fd, data, offset):
                nonlocal count
                count += 1
                if count == 1:
                    raise InterruptedError()
                return device.write(fd, data[:1031], offset)
            device.pwrite.side_effect = short_write
            fb = display.Framebuffer()
            fb.draw(Image.new("RGB", (428, 142), "white"))
            self.assertEqual(device.memory, b"\xff" * len(device.memory))
            self.assertGreater(count, 100)
            fb.close()

    def test_zero_negative_overreported_and_failed_writes_propagate(self):
        for result in (0, -1, 428 * 142 * 2 + 1, OSError(errno.EIO, "fixture disconnected")):
            with self.subTest(result=result), FakeDevice() as device:
                fb = display.Framebuffer()
                device.pwrite.side_effect = result if isinstance(result, Exception) else None
                device.pwrite.return_value = result
                with self.assertRaises((display.DisplayError, OSError)):
                    fb.draw(Image.new("RGB", (428, 142)))
                fb.close()

    def test_changed_mode_rejected_before_any_write(self):
        with FakeDevice() as device:
            fb = display.Framebuffer()
            device.var.red.offset, device.var.blue.offset = 0, 11
            with self.assertRaisesRegex(display.DisplayError, "mode changed"):
                fb.draw(Image.new("RGB", (428, 142)))
            device.pwrite.assert_not_called()
            fb.close()


class ConfigTests(unittest.TestCase):
    def test_config_wrapper_delegates_defaults_and_reloads_without_reinterpretation(self):
        first, second = fixture_config(), fixture_config(False, 70, "storage", 60)
        fake = SimpleNamespace(load_display_config=mock.Mock(side_effect=[first, second]))
        with mock.patch.object(e87n, "hardware", fake, create=True), \
                mock.patch.dict(sys.modules, {"e87n.hardware": fake}):
            self.assertIs(display.load_display_config(), first)
            self.assertIs(display.load_display_config(), second)
            self.assertEqual(fake.load_display_config.call_args_list, [mock.call(), mock.call()])

    def test_shared_validator_errors_propagate_without_defaults_or_clamping(self):
        for reason in ("not root-owned", "symlink", "extra keys", "refresh_seconds must be an integer in 2..60"):
            fake = SimpleNamespace(load_display_config=mock.Mock(side_effect=ValueError(reason)))
            with self.subTest(reason=reason), mock.patch.object(e87n, "hardware", fake, create=True), \
                    mock.patch.dict(sys.modules, {"e87n.hardware": fake}):
                with self.assertRaisesRegex(ValueError, reason):
                    display.load_display_config()


class RendererTests(unittest.TestCase):
    def setUp(self):
        directory = fixture_font_directory()
        self.fonts = mock.patch.object(display, "FONT_DIRECTORY", directory)
        self.fonts.start()
        display._font.cache_clear()
        display._cjk_font.cache_clear()
        self.addCleanup(self.fonts.stop)
        self.addCleanup(display._font.cache_clear)
        self.addCleanup(display._cjk_font.cache_clear)

    def capture_render(self, snapshot, screen, theme="dark"):
        image, texts, boxes, _ = self.capture_render_layout(snapshot, screen, theme)
        return image, texts, boxes

    def capture_render_layout(self, snapshot, screen, theme="dark"):
        texts, boxes = [], []
        panels = []
        original_text = ImageDraw.ImageDraw.text
        original_rectangle = ImageDraw.ImageDraw.rounded_rectangle
        def record_text(draw, xy, text, *args, **kwargs):
            texts.append(text)
            boxes.append(draw.textbbox(xy, text, font=kwargs["font"], anchor=kwargs.get("anchor")))
            return original_text(draw, xy, text, *args, **kwargs)
        def record_rectangle(draw, xy, *args, **kwargs):
            panels.append(tuple(xy))
            return original_rectangle(draw, xy, *args, **kwargs)
        with mock.patch.object(ImageDraw.ImageDraw, "text", new=record_text), \
                mock.patch.object(ImageDraw.ImageDraw, "rounded_rectangle", new=record_rectangle):
            image = display.render(snapshot, screen, theme=theme)
        return image, texts, boxes, panels

    def test_all_pages_legible_in_bounds_and_distinct(self):
        images = set()
        for screen in display.SCREENS:
            with self.subTest(screen=screen):
                image, texts, boxes, panels = self.capture_render_layout(
                    display.preview_snapshot(), screen)
                self.assertEqual(image.size, (428, 142))
                self.assertEqual(image.mode, "RGB")
                self.assertGreaterEqual(len(texts), 4)
                self.assertGreater(len(image.getcolors(maxcolors=100000)), 20)
                for left, top, right, bottom in boxes + panels:
                    self.assertGreaterEqual(left, 0)
                    self.assertGreaterEqual(top, 0)
                    self.assertLessEqual(right, 428)
                    self.assertLessEqual(bottom, 142)
                images.add(image.tobytes())
        self.assertEqual(len(images), len(display.SCREENS))

    def test_missing_measurements_do_not_invent_values_or_render_rpm(self):
        for screen in display.SCREENS:
            with self.subTest(screen=screen):
                _, texts, _ = self.capture_render({}, screen)
                self.assertNotIn("0", texts)
                self.assertNotIn("0.0 C", texts)
                self.assertFalse(any("RPM" in str(text) for text in texts))
        snapshot = display.preview_snapshot()
        _, texts, _ = self.capture_render(snapshot, "thermal")
        self.assertIn("75%", texts)
        self.assertIn("L2/3", texts)
        self.assertNotIn("192", texts)
        self.assertIn("无测速", texts)
        snapshot["fan"]["rpm"] = 1234
        _, texts, _ = self.capture_render(snapshot, "overview")
        self.assertNotIn("1234", texts)
        self.assertFalse(any("RPM" in str(text) for text in texts))

    def test_zero_values_are_real_and_absent_values_stay_missing(self):
        snapshot = {"cpu_usage_percent": 0, "cpu_temp_mc": 0, "phy_temp_mc": None, "loadavg": [0, 0, 0],
                    "fan": {"rpm": 0}, "mem_total_kib": 1024, "mem_available_kib": 1024,
                    "uptime_seconds": 0}
        _, texts, _ = self.capture_render(snapshot, "overview")
        for text in ("0.0 C", "0%"):
            self.assertIn(text, texts)
        for text in ("网口1", "无IP", "风扇"):
            self.assertNotIn(text, texts)

    def test_overview_shows_assigned_ip_usage_ram_temperature_and_fan(self):
        snapshot = display.preview_snapshot()
        _, texts, _ = self.capture_render(snapshot, "overview")
        for text in ("E87N  /  系统", "Debian 13", "在线", "网口1", "192.0.2.87",
                     "网口2", "198.51.100.23",
                     "CPU", "24%", "内存", "38%", "温度", "58.8 C", "风扇",
                     "自动 75%"):
            self.assertIn(text, texts)
        self.assertNotIn("无IP", texts)
        self.assertNotIn("0.42", texts)  # Load average is not CPU usage.
        snapshot.pop("cpu_usage_percent")
        _, texts, _ = self.capture_render(snapshot, "overview")
        self.assertNotIn("CPU", texts)

    def test_overview_uses_available_fan_pwm_but_ignores_tachometer(self):
        snapshot = display.preview_snapshot()
        for fan, detail in (
            ({"rpm": 0, "pwm": 0}, "0%"),
            ({"rpm": 1234, "pwm": 192}, "75%"),
        ):
            with self.subTest(fan=fan):
                snapshot["fan"] = fan
                _, texts, _ = self.capture_render(snapshot, "overview")
                self.assertIn("风扇", texts)
                self.assertIn(detail, texts)
                self.assertNotIn("--", texts)
                self.assertFalse(any("RPM" in str(text) for text in texts))
        for fan in ({"rpm": -1, "pwm": 256, "state": 4, "max_state": 3},
                    {"rpm": True, "pwm": True}):
            with self.subTest(fan=fan):
                snapshot["fan"] = fan
                _, texts, _ = self.capture_render(snapshot, "overview")
                self.assertNotIn("风扇", texts)
                self.assertFalse(any("RPM" in str(text) for text in texts))

    def test_overview_prefers_up_ipv4_then_ipv6_and_marks_socket_free_fallback(self):
        data = {"network": [
            {"name": "down0", "carrier": 0, "ipv4": "192.0.2.1"},
            {"name": "unknown0", "ipv4": "192.0.2.2"},
            {"name": "end0", "carrier": 1, "ipv4": "192.0.2.87", "ipv6": ["2001:db8::87"]},
        ]}
        self.assertEqual(display._overview_address(data), ("end0", "192.0.2.87"))
        data["network"][2]["ipv4"] = None
        self.assertEqual(display._overview_address(data), ("end0", "2001:db8::87"))
        data["network"] = [{"name": "br-lan", "carrier": 1, "ipv6": ["fe80::87"]}]
        data["local_ipv4"] = ["192.0.2.87"]
        self.assertEqual(display._overview_address(data), ("本机 IPv4", "192.0.2.87"))
        data["local_ipv4"] = []
        self.assertEqual(display._overview_address(data), ("无地址", "--"))
        data["network"][0]["carrier"] = 0
        self.assertEqual(display._overview_address(data), ("无地址", "--"))

    def test_overview_invalid_addresses_and_percentages_are_unknown(self):
        for value in (None, [], True, 1234, "", "0.0.0.0", "127.0.0.1", "224.0.0.1",
                      "192.0.2.87\n", "999.1.2.3", "x" * 500):
            with self.subTest(value=value):
                data = {"network": [{"name": "end0", "carrier": 1, "ipv4": value}],
                        "local_ipv4": [value]}
                self.assertEqual(display._overview_address(data), ("无地址", "--"))
        for value in ("::", "::1", "ff02::1", "fe80::1%end0", "192.0.2.87", "bad"):
            data = {"network": [{"name": "end0", "ipv6": [value]}]}
            self.assertEqual(display._overview_address(data), ("无地址", "--"))
        for value in (None, -1, 101, float("nan"), float("inf"), True, "50"):
            _, texts, _ = self.capture_render({"cpu_usage_percent": value}, "overview")
            self.assertNotIn("CPU", texts)

    def test_overview_full_addresses_and_extreme_values_do_not_clip_or_overlap(self):
        snapshot = display.preview_snapshot()
        snapshot.update(cpu_usage_percent=100, mem_available_kib=0, cpu_temp_mc=200000)
        snapshot["fan"] = {"rpm": 200000, "pwm": 255}
        for address in ("192.168.100.200", "2001:db8:abcd:abcd:abcd:abcd:abcd:abcd"):
            snapshot["network"] = [{"name": "enx0123456789ab", "carrier": 1,
                                    "ipv4": address if "." in address else None,
                                    "ipv6": [address] if ":" in address else []}]
            _, texts, boxes = self.capture_render(snapshot, "overview")
            self.assertNotIn("200000", texts)
            self.assertIn("200.0 C", texts)
            for index, (left, top, right, bottom) in enumerate(boxes):
                self.assertTrue(0 <= left <= right <= 428 and 0 <= top <= bottom <= 142)
                for other_left, other_top, other_right, other_bottom in boxes[index + 1:]:
                    self.assertFalse(left < other_right and other_left < right
                                     and top < other_bottom and other_top < bottom, texts)

    def test_network_shows_cumulative_totals_without_rates(self):
        _, texts, _ = self.capture_render(display.preview_snapshot(), "network")
        for text in ("网络  /  网口", "网口1", "网口2", "接收 31.8 GiB",
                     "发送 7.6 GiB", "接收 1.1 GiB", "发送 329.7 MiB", "在线"):
            self.assertIn(text, texts)
        self.assertNotIn("断开", texts)
        self.assertFalse(any("/s" in text for text in texts))

    def test_traffic_shows_aggregate_realtime_rates(self):
        _, texts, _ = self.capture_render(display.preview_snapshot(), "traffic")
        for text in ("接收累计", "发送累计", "实时", "1.5 MiB/s", "768.0 KiB/s"):
            self.assertIn(text, texts)

        partial = display.preview_snapshot()
        partial["network"][1]["rx_bytes_per_second"] = None
        _, texts, _ = self.capture_render(partial, "traffic")
        self.assertNotIn("1.5 MiB/s", texts)
        self.assertIn("--", texts)
        self.assertIn("768.0 KiB/s", texts)

    def test_single_active_port_hides_idle_port_and_uses_full_width_card(self):
        snapshot = {"uptime_seconds": 60, "network": [
            {"name": "eth0", "carrier": 1, "ipv4": "192.0.2.87", "ipv6": [],
             "rx_bytes": 1234, "tx_bytes": 5678},
            {"name": "eth1", "carrier": 0, "ipv4": "198.51.100.87",
             "ipv6": ["2001:db8::87", "fe80::87"],
             "rx_bytes": 9999, "tx_bytes": 8888},
        ]}
        for screen in ("overview", "network"):
            with self.subTest(screen=screen):
                _, texts, _, panels = self.capture_render_layout(snapshot, screen)
                self.assertIn("网口1", texts)
                self.assertIn("eth0", texts)
                self.assertNotIn("网口2", texts)
                self.assertNotIn("eth1", texts)
                self.assertNotIn("断开", texts)
                if screen == "overview":
                    full_width = [box for box in panels
                                  if box[0] <= 12 and box[2] >= 416
                                  and box[1] <= 55 and box[3] >= 90]
                else:
                    full_width = [box for box in panels
                                  if box[0] <= 12 and box[2] >= 416
                                  and box[1] <= 40 and box[3] >= 130]
                self.assertTrue(full_width, panels)

    def test_unknown_carrier_requires_ipv4_or_global_ipv6(self):
        snapshot = {"network": [
            {"name": "eth0", "carrier": None, "ipv6": ["fe80::87"]},
            {"name": "eth1", "carrier": None, "ipv6": ["2001:db8::87"]},
        ]}
        self.assertEqual([item["name"] for item in display._network_items(snapshot)], ["eth1"])
        _, texts, _, panels = self.capture_render_layout(snapshot, "network")
        self.assertIn("网口2", texts)
        self.assertIn("eth1", texts)
        self.assertNotIn("网口1", texts)
        self.assertNotIn("eth0", texts)
        self.assertTrue(any(box[0] <= 12 and box[2] >= 416 for box in panels), panels)

    def test_two_active_ports_keep_two_cards(self):
        snapshot = {"uptime_seconds": 60, "network": [
            {"name": "eth0", "carrier": 1, "ipv4": "192.0.2.87", "ipv6": [],
             "rx_bytes": 1234, "tx_bytes": 5678},
            {"name": "eth1", "carrier": 1, "ipv4": "198.51.100.87", "ipv6": [],
             "rx_bytes": 9012, "tx_bytes": 3456},
        ]}
        for screen in ("overview", "network"):
            with self.subTest(screen=screen):
                _, texts, _, panels = self.capture_render_layout(snapshot, screen)
                for text in ("网口1", "eth0", "192.0.2.87", "网口2", "eth1", "198.51.100.87"):
                    self.assertIn(text, texts)
                if screen == "overview":
                    cards = [box for box in panels if 48 <= box[1] <= 56 and 90 <= box[3] <= 98]
                else:
                    cards = [box for box in panels if 35 <= box[1] <= 42 and box[3] >= 130]
                self.assertEqual(len(cards), 2, panels)
                self.assertTrue(all(185 <= box[2] - box[0] <= 205 for box in cards), cards)

    def test_socket_free_ipv4_fallback_is_visible_without_repeating_ipv6(self):
        snapshot = {"local_ipv4": ["192.0.2.87"], "network": [
            {"name": "end0", "carrier": 1, "ipv6": ["fe80::87"],
             "rx_bytes": 1, "tx_bytes": 2},
        ]}
        _, overview, _ = self.capture_render(snapshot, "overview")
        self.assertIn("本机 IPv4", overview)
        self.assertIn("192.0.2.87", overview)
        self.assertNotIn("fe80::87", overview)
        _, network, _ = self.capture_render(snapshot, "network")
        self.assertIn("本机 IPv4", network)
        self.assertIn("192.0.2.87", network)
        self.assertEqual(network.count("fe80::87"), 1)

    def test_storage_hides_devices_without_temperature_data(self):
        snapshot = {"uptime_seconds": 60, "storage": [
            {"name": "nvme0", "temp_mc": None},
            {"name": "nvme1", "temp_mc": 42000},
        ]}
        _, texts, _ = self.capture_render(snapshot, "storage")
        self.assertNotIn("nvme0", texts)
        self.assertIn("nvme1", texts)
        self.assertIn("42.0 C", texts)

    def test_cpu_memory_fan_and_traffic_pages_render_real_semantics(self):
        snapshot = display.preview_snapshot()
        expectations = {
            "cpu": ("24%", "0.42", "0.31", "0.28", "1800 MHz", "ondemand", "cpufreq-dt"),
            "memory": ("38%", "已用 384.0 MiB", "可用 640.0 MiB", "1.0 GiB"),
            "fan": ("自动", "档位", "L2/3", "75%", "step_wise", "无测速"),
            "traffic": ("32.9 GiB", "7.9 GiB", "1.5 MiB/s", "768.0 KiB/s",
                        "end0", "192.0.2.87"),
        }
        for screen, expected in expectations.items():
            with self.subTest(screen=screen):
                _, texts, _ = self.capture_render(snapshot, screen)
                for text in expected:
                    self.assertIn(text, texts)
                if screen == "fan":
                    self.assertNotIn("kernel-thermal", texts)
                self.assertFalse(any("RPM" in str(text) for text in texts))

    def test_rotation_pages_require_effective_live_data(self):
        requested = ["cpu", "memory", "thermal", "fan", "network", "traffic", "storage"]
        invalid = {
            "cpu_usage_percent": None,
            "cpu_frequency": {"current_khz": 0, "governor": "", "driver": "  "},
            "loadavg": [None, None, None],
            "mem_total_kib": 0,
            "mem_available_kib": None,
            "cpu_temp_mc": float("nan"),
            "phy_temp_mc": None,
            "fan": {"state": 0, "max_state": None, "pwm": None, "pwm_percent": None,
                    "control": None, "mode": None, "policy": None},
            "network": [{"name": "eth0", "carrier": 0, "ipv4": None, "ipv6": [],
                         "rx_bytes": None, "tx_bytes": None}],
            "storage": [{"name": "nvme0", "temp_mc": None}],
        }
        self.assertEqual(display._available_pages(invalid, requested), ["overview"])
        self.assertEqual(display._available_pages({}, requested), ["overview"])
        self.assertEqual(display._available_pages({}, ["overview"] + requested), ["overview"])

        cases = {
            "cpu": {"cpu_usage_percent": 0},
            "memory": {"mem_total_kib": 1024, "mem_available_kib": 1024},
            "thermal": {"cpu_temp_mc": 0},
            "fan": {"fan": {"state": 0, "max_state": 3}},
            "network": {"network": [{"name": "eth0", "carrier": 1, "ipv4": None, "ipv6": []}]},
            "traffic": {"network": [{"name": "eth0", "carrier": 1,
                                      "rx_bytes": 0, "tx_bytes": None}]},
            "storage": {"storage": [{"name": "nvme0", "temp_mc": 0}]},
        }
        for page, snapshot in cases.items():
            with self.subTest(page=page):
                self.assertEqual(display._available_pages(snapshot, [page]), [page])

        sparse = {"cpu_usage_percent": 12.5,
                  "storage": [{"name": "nvme0", "temp_mc": 41000}]}
        self.assertEqual(display._available_pages(sparse, requested), ["cpu", "storage"])

        linked_without_counters = {
            "network": [{"name": "eth0", "carrier": 1,
                         "rx_bytes": None, "tx_bytes": None}],
        }
        self.assertEqual(display._available_pages(linked_without_counters,
                                                  ["network", "traffic"]), ["network"])

    def test_malformed_snapshot_fields_do_not_invent_values_or_overrun(self):
        data = {"cpu_temp_mc": float("nan"), "phy_temp_mc": "40", "fan": None,
                "loadavg": [], "mem_total_kib": 100, "mem_available_kib": 101,
                "uptime_seconds": -1,
                "network": [{"name": "eth\n" + "x" * 200, "rx_bytes": -1,
                             "tx_bytes": None, "carrier": "up"}] * 5,
                "storage": [{"name": "nvme\t" + "x" * 200, "temp_mc": None}] * 4}
        for screen in display.SCREENS:
            _, texts, boxes = self.capture_render(data, screen)
            self.assertFalse(any("\n" in text or "\t" in text for text in texts))
            self.assertTrue(all(0 <= left <= right <= 428 and 0 <= top <= bottom <= 142
                                for left, top, right, bottom in boxes))
        _, texts, _ = self.capture_render(data, "network")
        self.assertNotIn("网口1", texts)
        self.assertNotIn("无IPv4", texts)
        self.assertNotIn("无IPv6", texts)

    def test_invalid_snapshot_root_or_screen_is_an_error(self):
        with self.assertRaises(display.DisplayError):
            display.render(None)
        with self.assertRaises(display.DisplayError):
            display.render({}, "invalid")
        with self.assertRaises(display.DisplayError):
            display.render({}, theme="invalid")

    def test_all_themes_render_distinct_palettes(self):
        images = set()
        for theme in display.THEMES:
            with self.subTest(theme=theme):
                image, texts, boxes = self.capture_render(display.preview_snapshot(), "overview", theme)
                self.assertEqual(image.size, (428, 142))
                self.assertGreater(len(texts), 4)
                self.assertTrue(all(0 <= left <= right <= 428 and 0 <= top <= bottom <= 142
                                    for left, top, right, bottom in boxes))
                images.add(image.tobytes())
        self.assertEqual(len(images), len(display.THEMES))

    def test_all_themes_support_all_rotation_screens(self):
        for theme in display.THEMES:
            for screen in display.SCREENS:
                with self.subTest(theme=theme, screen=screen):
                    image, _, boxes, panels = self.capture_render_layout(
                        display.preview_snapshot(), screen, theme)
                    self.assertEqual(image.size, (428, 142))
                    self.assertTrue(all(0 <= left <= right <= 428 and 0 <= top <= bottom <= 142
                                        for left, top, right, bottom in boxes + panels))

    def test_preview_cli_all_layouts_never_uses_hardware_or_live_config(self):
        original_import = __import__
        def no_hardware_import(name, globals=None, locals=None, fromlist=(), level=0):
            if "hardware" in name.split(".") or "hardware" in (fromlist or ()):
                raise AssertionError("preview imported hardware")
            return original_import(name, globals, locals, fromlist, level)
        with tempfile.TemporaryDirectory(prefix="e87n-display-test-") as directory:
            with mock.patch("builtins.__import__", side_effect=no_hardware_import), \
                    mock.patch("io.open", side_effect=guard_hardware_paths(io.open)), \
                    mock.patch("builtins.open", side_effect=guard_hardware_paths(open)), \
                    mock.patch.object(display.os, "stat", side_effect=guard_hardware_paths(os.stat)), \
                    mock.patch.object(display.os, "lstat", side_effect=guard_hardware_paths(os.lstat)), \
                    mock.patch.object(display, "Framebuffer", side_effect=AssertionError("real framebuffer")), \
                    mock.patch.object(display, "read_snapshot", side_effect=AssertionError("live snapshot")), \
                    mock.patch.object(display, "load_display_config", side_effect=AssertionError("live config")), \
                    mock.patch.object(display.os, "open", side_effect=guard_hardware_paths(os.open)), \
                    mock.patch.object(display.fcntl, "ioctl", side_effect=AssertionError("real ioctl")):
                for screen in display.SCREENS:
                    output = Path(directory) / (screen + ".png")
                    self.assertEqual(display.main(["--preview", str(output), "--screen", screen]), 0)
                    with Image.open(output) as image:
                        self.assertEqual(image.format, "PNG")
                        self.assertEqual(image.size, (428, 142))
                        self.assertEqual(image.convert("RGB").tobytes(), display.render(display.preview_snapshot(), screen).tobytes())

    def test_fresh_module_preview_does_not_import_hardware(self):
        # Check the real package initializer too, in a fresh isolated interpreter.
        # A meta-path hook rejects hardware before any of its code can execute.
        script = r'''
import builtins, io, os, runpy, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv.pop(1))
class OfflineImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "e87n.hardware":
            raise AssertionError("offline preview imported e87n.hardware via package initializer")
sys.meta_path.insert(0, OfflineImports())
def guard(function):
    def call(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            path_text = os.fsdecode(path)
            if any(path_text == root or path_text.startswith(root + "/") for root in ("/dev", "/sys", "/proc")):
                raise AssertionError("offline preview touched " + path_text)
        return function(path, *args, **kwargs)
    return call
builtins.open = guard(builtins.open)
io.open = guard(io.open)
os.open = guard(os.open)
os.stat = guard(os.stat)
os.lstat = guard(os.lstat)
runpy.run_module("e87n.display", run_name="__main__")
'''
        source = str(Path(__file__).resolve().parents[1] / "board-support")
        with tempfile.TemporaryDirectory(prefix="e87n-offline-module-") as directory:
            for screen in display.SCREENS:
                output = str(Path(directory) / (screen + ".png"))
                result = subprocess.run([sys.executable, "-I", "-B", "-c", script, source,
                                         "--preview", output, "--screen", screen,
                                         "--font-dir", str(fixture_font_directory())],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                with Image.open(output) as image:
                    self.assertEqual(image.size, (428, 142))


class DaemonTests(unittest.TestCase):
    def test_disabled_never_opens_device_or_collects_or_renders(self):
        with mock.patch.object(display, "load_display_config", return_value=fixture_config(False)), \
                mock.patch.object(display, "Framebuffer") as factory, \
                mock.patch.object(display, "read_snapshot") as snapshot, \
                mock.patch.object(display, "render") as renderer, \
                mock.patch.object(display.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(display.main(["--daemon"]), 0)
            factory.assert_not_called()
            snapshot.assert_not_called()
            renderer.assert_not_called()

    def test_reloads_enabled_screen_and_refresh_with_fixed_page_fallback(self):
        configs = [fixture_config(False, 20, "overview", 2),
                   fixture_config(True, 30, "thermal", 3),
                   fixture_config(False, 40, "network", 4),
                   fixture_config(True, 50, "storage", 60)]
        with mock.patch.object(display, "load_display_config", side_effect=configs) as config, \
                mock.patch.object(display, "Framebuffer") as factory, \
                mock.patch.object(display, "read_snapshot", return_value={}) as snapshot, \
                mock.patch.object(display, "render", return_value="fixture image") as renderer, \
                mock.patch.object(display.time, "sleep", side_effect=[None, None, None, KeyboardInterrupt]) as sleep:
            self.assertEqual(display.main(["--daemon"]), 0)
            self.assertEqual(config.call_count, 4)
            factory.assert_called_once_with()
            self.assertEqual(snapshot.call_count, 2)
            self.assertEqual(renderer.call_args_list, [mock.call({}, "overview", theme="dark"),
                                                       mock.call({}, "overview", theme="dark")])
            self.assertEqual(sleep.call_args_list, [mock.call(2), mock.call(3), mock.call(4), mock.call(60)])
            self.assertEqual(factory.return_value.draw.call_count, 2)
            factory.return_value.close.assert_called_once_with()

    def test_fixed_page_returns_when_telemetry_recovers(self):
        config = fixture_config(True, screen="storage", rotation_enabled=False, refresh=2)
        snapshots = [{}, {"storage": [{"name": "nvme0", "temp_mc": 41000}]}]
        with mock.patch.object(display, "load_display_config", return_value=config), \
                mock.patch.object(display, "Framebuffer") as factory, \
                mock.patch.object(display, "read_snapshot", side_effect=snapshots), \
                mock.patch.object(display, "render", return_value="fixture image") as renderer, \
                mock.patch.object(display.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            self.assertEqual(display.main(["--daemon"]), 0)
        self.assertEqual(renderer.call_args_list,
                         [mock.call(snapshots[0], "overview", theme="dark"),
                          mock.call(snapshots[1], "storage", theme="dark")])
        factory.return_value.close.assert_called_once_with()

    def test_rotation_skips_unavailable_pages_at_configured_interval(self):
        config = fixture_config(True, screen="storage", theme="aurora", rotation_enabled=True, rotation_seconds=3,
                                rotation_screens=["overview", "network"], refresh=2)
        with mock.patch.object(display, "load_display_config", return_value=config), \
                mock.patch.object(display, "Framebuffer") as factory, \
                mock.patch.object(display, "read_snapshot", return_value={}), \
                mock.patch.object(display, "render", return_value="fixture image") as renderer, \
                mock.patch.object(display.time, "monotonic", side_effect=[0.0, 0.0, 2.0, 2.0, 3.1, 3.1]), \
                mock.patch.object(display.time, "sleep", side_effect=[None, None, KeyboardInterrupt]):
            self.assertEqual(display.main(["--daemon"]), 0)
        self.assertEqual(renderer.call_args_list, [mock.call({}, "overview", theme="aurora"),
                                                   mock.call({}, "overview", theme="aurora"),
                                                   mock.call({}, "overview", theme="aurora")])
        factory.return_value.close.assert_called_once_with()

    def test_rotation_leaves_optional_page_when_live_data_disappears(self):
        config = fixture_config(True, screen="fan", rotation_enabled=True,
                                rotation_screens=["fan", "storage"], refresh=2)
        snapshots = [{"fan": {"control": "kernel-thermal"}}, {}]
        with mock.patch.object(display, "load_display_config", return_value=config), \
                mock.patch.object(display, "Framebuffer") as factory, \
                mock.patch.object(display, "read_snapshot", side_effect=snapshots), \
                mock.patch.object(display, "render", return_value="fixture image") as renderer, \
                mock.patch.object(display.time, "monotonic", side_effect=[0.0, 0.0, 1.0, 1.0]), \
                mock.patch.object(display.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            self.assertEqual(display.main(["--daemon"]), 0)
        self.assertEqual(renderer.call_args_list, [mock.call(snapshots[0], "fan", theme="dark"),
                                                   mock.call(snapshots[1], "overview", theme="dark")])
        factory.return_value.close.assert_called_once_with()

    def test_failures_report_nonzero_close_and_do_not_retry(self):
        for stage in ("config", "open", "snapshot", "render", "write"):
            with self.subTest(stage=stage), ExitStack() as stack:
                config = stack.enter_context(mock.patch.object(display, "load_display_config", return_value=fixture_config()))
                factory = stack.enter_context(mock.patch.object(display, "Framebuffer"))
                snapshot = stack.enter_context(mock.patch.object(display, "read_snapshot", return_value={}))
                renderer = stack.enter_context(mock.patch.object(display, "render"))
                sleep = stack.enter_context(mock.patch.object(display.time, "sleep"))
                stderr = stack.enter_context(mock.patch.object(sys, "stderr", new_callable=io.StringIO))
                target = {"config": config, "open": factory, "snapshot": snapshot,
                          "render": renderer, "write": factory.return_value.draw}[stage]
                target.side_effect = OSError(errno.EIO, "fixture " + stage)
                self.assertEqual(display.main(["--daemon"]), 1)
                self.assertIn("fixture " + stage, stderr.getvalue())
                sleep.assert_not_called()
                self.assertLessEqual(factory.call_count, 1)
                if stage not in ("config", "open"):
                    factory.return_value.close.assert_called_once_with()

    def test_hardware_snapshot_api_import_is_lazy(self):
        fake = SimpleNamespace(snapshot=mock.Mock(return_value={"cpu_temp_mc": None}))
        with mock.patch.object(e87n, "hardware", fake, create=True), \
                mock.patch.dict(sys.modules, {"e87n.hardware": fake}), \
                mock.patch.object(display.os, "open", side_effect=AssertionError("live hardware read")):
            self.assertEqual(display.read_snapshot(), {"cpu_temp_mc": None})
            fake.snapshot.assert_called_once_with()

    def test_cli_rejects_conflicting_modes_and_daemon_screen(self):
        for arguments in ([], ["--preview", "x.png", "--daemon"], ["--daemon", "--screen", "network"],
                          ["--daemon", "--theme", "dark"],
                          ["--daemon", "--font-dir", "/tmp/fonts"],
                          ["--preview", "x.png", "--screen", "invalid"]):
            with self.subTest(arguments=arguments), mock.patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    display.main(arguments)
                self.assertEqual(caught.exception.code, 2)

    def test_preview_refuses_hardware_output_without_drawing(self):
        for path in ("/dev/fb0", "/sys/something.png", "/proc/1/mem"):
            with self.subTest(path=path), mock.patch.object(display, "render") as renderer, \
                    mock.patch.object(Path, "resolve", side_effect=AssertionError("hardware path resolved")), \
                    mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
                self.assertEqual(display.main(["--preview", path]), 1)
                self.assertIn("preview output must not be a hardware path", stderr.getvalue())
                renderer.assert_not_called()

    def test_preview_rejects_device_symlinks_before_following_them(self):
        with tempfile.TemporaryDirectory(prefix="e87n-preview-links-") as directory:
            for index, target in enumerate(("/dev/fb0", "/sys", "/proc/1/mem")):
                link = Path(directory) / str(index)
                link.symlink_to(target)
                destination = link / "brightness" if target == "/sys" else link
                with self.subTest(target=target), \
                        mock.patch.object(os, "stat", side_effect=guard_hardware_paths(os.stat)), \
                        mock.patch.object(os, "lstat", side_effect=guard_hardware_paths(os.lstat)), \
                        mock.patch.object(os, "open", side_effect=guard_hardware_paths(os.open)), \
                        mock.patch.object(display, "render") as renderer, \
                        mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
                    self.assertEqual(display.main(["--preview", str(destination)]), 1)
                    self.assertIn("preview output must not be a hardware path", stderr.getvalue())
                    renderer.assert_not_called()

    def test_preview_refuses_hardlinked_regular_file_without_truncating(self):
        with tempfile.TemporaryDirectory(prefix="e87n-preview-hardlink-") as directory:
            original, linked = Path(directory) / "original", Path(directory) / "linked"
            original.write_bytes(b"keep fixture")
            os.link(original, linked)
            with self.assertRaisesRegex(display.DisplayError, "hard links"):
                display._save_preview(Image.new("RGB", (428, 142)), linked)
            self.assertEqual(original.read_bytes(), b"keep fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
