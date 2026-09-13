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


def fixture_config(enabled=True, brightness=20, screen="overview", refresh=2):
    return {"enabled": enabled, "brightness_percent": brightness,
            "screen": screen, "refresh_seconds": refresh}


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

    def test_reject_wrong_id_geometry_format_memory_and_overflow(self):
        cases = [
            ("fix", "id", b"simpledrm"), ("fix", "id", b"fb_nv3007-other"),
            ("var", "xres", 142), ("var", "yres", 428),
            ("var", "bits_per_pixel", 32), ("var", "grayscale", 1),
            ("var", "nonstd", 1), ("var", "vmode", 256),
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
        self.addCleanup(self.fonts.stop)
        self.addCleanup(display._font.cache_clear)

    def capture_render(self, snapshot, screen):
        texts, boxes = [], []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            texts.append(text)
            boxes.append(draw.textbbox(xy, text, font=kwargs["font"], anchor=kwargs.get("anchor")))
            return original(draw, xy, text, *args, **kwargs)
        with mock.patch.object(ImageDraw.ImageDraw, "text", new=record):
            image = display.render(snapshot, screen)
        return image, texts, boxes

    def test_all_four_layouts_legible_in_bounds_and_distinct(self):
        images = set()
        for screen in display.SCREENS:
            with self.subTest(screen=screen):
                image, texts, boxes = self.capture_render(display.preview_snapshot(), screen)
                self.assertEqual(image.size, (428, 142))
                self.assertEqual(image.mode, "RGB")
                self.assertGreater(len(texts), 4)
                self.assertGreater(len(image.getcolors(maxcolors=100000)), 20)
                for left, top, right, bottom in boxes:
                    self.assertGreaterEqual(left, 0)
                    self.assertGreaterEqual(top, 0)
                    self.assertLessEqual(right, 428)
                    self.assertLessEqual(bottom, 142)
                images.add(image.tobytes())
        self.assertEqual(len(images), 4)

    def test_missing_measurements_are_dashes_and_never_inferred_rpm(self):
        for screen in display.SCREENS:
            with self.subTest(screen=screen):
                _, texts, _ = self.capture_render({}, screen)
                self.assertIn("--", texts)
                self.assertNotIn("0", texts)
                self.assertNotIn("0.0 C", texts)
        snapshot = display.preview_snapshot()
        _, texts, _ = self.capture_render(snapshot, "thermal")
        self.assertIn("--", texts)
        self.assertIn("192/255", texts)
        self.assertIn("2/3", texts)
        self.assertNotIn("192", texts)
        snapshot["fan"]["rpm"] = 1234
        _, texts, _ = self.capture_render(snapshot, "overview")
        self.assertIn("1234", texts)

    def test_zero_values_are_real_and_absent_values_stay_missing(self):
        snapshot = {"cpu_temp_mc": 0, "phy_temp_mc": None, "loadavg": [0, 0, 0],
                    "fan": {"rpm": 0}, "mem_total_kib": 1024, "mem_available_kib": 1024,
                    "uptime_seconds": 0}
        _, texts, _ = self.capture_render(snapshot, "overview")
        for text in ("0.0 C", "--", "0.00", "0", "0%", "0h 00m"):
            self.assertIn(text, texts)

    def test_network_is_totals_and_link_unknown_is_not_down(self):
        _, texts, _ = self.capture_render(display.preview_snapshot(), "network")
        for text in ("NETWORK TOTALS", "RX TOTAL", "TX TOTAL", "UP", "DOWN", "--"):
            self.assertIn(text, texts)
        self.assertFalse(any("/s" in text for text in texts))

    def test_malformed_snapshot_fields_do_not_invent_values_or_overrun(self):
        data = {"cpu_temp_mc": float("nan"), "phy_temp_mc": "40", "fan": None,
                "loadavg": [], "mem_total_kib": 100, "mem_available_kib": 101,
                "uptime_seconds": -1,
                "network": [{"name": "eth\n" + "x" * 200, "rx_bytes": -1,
                             "tx_bytes": None, "carrier": "up"}] * 5,
                "storage": [{"name": "nvme\t" + "x" * 200, "temp_mc": None}] * 4}
        for screen in display.SCREENS:
            _, texts, boxes = self.capture_render(data, screen)
            self.assertIn("--", texts)
            self.assertFalse(any("\n" in text or "\t" in text for text in texts))
            self.assertTrue(all(0 <= left <= right <= 428 and 0 <= top <= bottom <= 142
                                for left, top, right, bottom in boxes))
        _, texts, _ = self.capture_render(data, "network")
        self.assertIn("+2 more", texts)

    def test_invalid_snapshot_root_or_screen_is_an_error(self):
        with self.assertRaises(display.DisplayError):
            display.render(None)
        with self.assertRaises(display.DisplayError):
            display.render({}, "invalid")

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

    def test_reloads_enabled_screen_and_refresh_every_loop(self):
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
            self.assertEqual(renderer.call_args_list, [mock.call({}, "thermal"), mock.call({}, "storage")])
            self.assertEqual(sleep.call_args_list, [mock.call(2), mock.call(3), mock.call(4), mock.call(60)])
            self.assertEqual(factory.return_value.draw.call_count, 2)
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
