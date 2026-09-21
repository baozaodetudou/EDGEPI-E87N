#!/usr/bin/env python3
"""Compile actual patched LVTS functions with ASan/UBSan and MMIO stubs.

Usage: python3 tests/test-lvts-kernel.py /path/to/pristine/pinned/kernel
This checks C behavior, not eFuse layout, physical IRQs or temperature accuracy.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
SOURCE = "drivers/thermal/mediatek/lvts_thermal.c"


def function(source, name):
    match = re.search(r"^static [^\n]+\b" + re.escape(name) + r"\(", source, re.M)
    if not match:
        raise AssertionError("missing actual driver function: " + name)
    begin = source.index("{", match.start())
    depth = 1
    end = begin + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <errno.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef int irqreturn_t;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((UINT32_MAX >> (31 - (h))) & (UINT32_MAX << (l)))
#define LVTS_SENSOR_MAX 4
#define LVTS_GOLDEN_TEMP_MAX 62
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define THERMAL_TRIP_VIOLATED 7
#define LVTS_MONINTSTS(base) ((base) + 0x10)
#define LVTS_INT_SENSOR0 0x0009001f
#define LVTS_INT_SENSOR1 0x001203e0
#define LVTS_INT_SENSOR2 0x00247c00
#define LVTS_INT_SENSOR3 0x1fc00000
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
struct device { int unused; };
static int dev_err_probe(struct device *dev, int err, const char *msg) {
    (void)dev; (void)msg; return err;
}
struct lvts_data {
    int gt_calib_bit_offset;
    bool require_valid_calibration;
    unsigned int def_calibration;
    int temp_offset;
};
struct lvts_sensor_data { int dt_id; unsigned int cal_offsets[3]; };
struct lvts_ctrl_data { unsigned int valid_sensor_mask; struct lvts_sensor_data lvts_sensor[4]; };
struct thermal_zone_device { int id; };
struct lvts_sensor { struct thermal_zone_device *tz; };
struct lvts_ctrl {
    const struct lvts_data *lvts_data;
    u32 calibration[4];
    unsigned int valid_sensor_mask;
    struct lvts_sensor sensors[4];
    u8 *base;
};
#define lvts_for_each_valid_sensor(i, ctrl) \
    for ((i) = 0; (i) < LVTS_SENSOR_MAX; (i)++) \
        if (!((ctrl)->valid_sensor_mask & BIT(i))) continue; else
static int golden_temp = 50;
static int golden_temp_offset;
static u32 irq_status, irq_acked;
static unsigned int updates;
static u32 readl(const void *addr) { (void)addr; return irq_status; }
static void writel(u32 value, void *addr) { (void)addr; irq_acked = value; }
static void thermal_zone_device_update(struct thermal_zone_device *tz, int event) {
    assert(tz != NULL);
    assert(irq_acked == irq_status); /* ACK must precede thermal callbacks. */
    assert(event == THERMAL_TRIP_VIOLATED);
    updates |= BIT(tz->id);
}
'''

TESTS = r'''
int main(void) {
    struct lvts_data data = { .gt_calib_bit_offset = E87N_GT_BIT_OFFSET };
    u32 gt, expected;
    unsigned int checks = 0;
    /* Exact allocations let ASan detect even a one-byte over-read. Reference
     * extraction is bit-by-bit and independent of the driver's byte decoder. */
    for (size_t len = 0; len <= 16; len++) {
        u8 *bytes = malloc(len ? len : 1);
        assert(bytes);
        for (size_t i = 0; i < len; i++) bytes[i] = (u8)(0x91 + 37 * i);
        for (int bit = 0; bit < 144; bit++) {
            data.gt_calib_bit_offset = bit;
            gt = 0xdeadbeef;
            int ret = lvts_calibration_gt(bytes, len, &data, &gt);
            if ((unsigned int)bit + 8 <= len * 8) {
                expected = 0;
                for (unsigned int b = 0; b < 8; b++) {
                    unsigned int source = bit + b;
                    expected |= ((bytes[source / 8] >> (source % 8)) & 1) << b;
                }
                assert(ret == 0 && gt == expected);
            } else {
                assert(ret == -EINVAL && gt == 0xdeadbeef);
            }
            checks++;
        }
        free(bytes);
    }
    u8 efuse[16] = {0, 0, 0, 37, 51, 0x12, 0x34, 0, 0x56, 0x78, 0x9a};
    data.gt_calib_bit_offset = E87N_GT_BIT_OFFSET;
    gt = 0xdeadbeef;
    assert(lvts_calibration_gt(efuse, 3, &data, &gt) == -EINVAL);
    assert(gt == 0xdeadbeef);
    assert(lvts_calibration_gt(efuse, 4, &data, &gt) == 0 && gt == 37);
    /* Generic offset=32 remains supported by the bounded decoder. It is not
     * the E87N layout selected by patch 833. */
    data.gt_calib_bit_offset = 32;
    assert(lvts_calibration_gt(efuse, 5, &data, &gt) == 0 && gt == 51);
    assert(lvts_calibration_gt(efuse, 4, &data, &gt) == -EINVAL);
    data.gt_calib_bit_offset = INT_MAX;
    assert(lvts_calibration_gt(efuse, sizeof(efuse), &data, &gt) == -EINVAL);
    data.gt_calib_bit_offset = -1;
    assert(lvts_calibration_gt(efuse, sizeof(efuse), &data, &gt) == -EINVAL);
    data.gt_calib_bit_offset = 32;
    assert(lvts_calibration_gt(NULL, 5, &data, &gt) == -EINVAL);
    assert(lvts_calibration_gt(efuse, 5, &data, NULL) == -EINVAL);
    u8 unaligned[17];
    memcpy(unaligned + 1, efuse, sizeof(efuse));
    assert(lvts_calibration_gt(unaligned + 1, 16, &data, &gt) == 0 && gt == 51);
    data.gt_calib_bit_offset = E87N_GT_BIT_OFFSET;
    assert(lvts_calibration_gt(unaligned + 1, 4, &data, &gt) == 0 && gt == 37);

    struct device dev = {0};
    struct lvts_ctrl_data ctrl_data = {
        .valid_sensor_mask = 3,
        .lvts_sensor = {{.cal_offsets = {4, 5, 6}}, {.cal_offsets = {8, 9, 10}}}
    };
    struct lvts_ctrl ctrl = {.lvts_data = &data};
    data.require_valid_calibration = true;
    data.def_calibration = 19380; /* Must never mask bad MT7987 data. */
    data.temp_offset = 204650;
    /* At bit24 the golden byte needs four bytes, not five. Sensor calibration
     * independently needs bytes through offset 10 (eleven bytes in total). */
    for (size_t len = 0; len <= 3; len++) {
        golden_temp = 41;
        golden_temp_offset = 1234;
        assert(lvts_golden_temp_init(&dev, efuse, len, &data) == -EINVAL);
        assert(golden_temp == 41 && golden_temp_offset == 1234);
        assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, len) == -EINVAL);
    }
    assert(lvts_golden_temp_init(&dev, efuse, 4, &data) == 0);
    assert(golden_temp == 37 && golden_temp_offset == 37 * 500 + 204650);
    for (size_t len = 4; len <= 10; len++)
        assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, len) == -EINVAL);
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 11) == 0);
    assert(ctrl.calibration[0] == 0x341233 && ctrl.calibration[1] == 0x9a7856);
    for (unsigned int invalid = 0; invalid <= 255; invalid++) {
        if (invalid > 0 && invalid < LVTS_GOLDEN_TEMP_MAX) continue;
        efuse[E87N_GT_BIT_OFFSET / 8] = invalid;
        golden_temp = 41;
        golden_temp_offset = 1234;
        gt = 0xdeadbeef;
        assert(lvts_calibration_gt(efuse, 16, &data, &gt) == -ENODATA);
        assert(gt == 0xdeadbeef);
        assert(lvts_golden_temp_init(&dev, efuse, 16, &data) == -ENODATA);
        assert(golden_temp == 41 && golden_temp_offset == 1234);
        assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 16) == -ENODATA);
    }
    efuse[E87N_GT_BIT_OFFSET / 8] = 37;
    assert(efuse[4] == 51); /* Invalid GT cases must not corrupt sensor data. */
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 10) == -EINVAL);
    memset(efuse + 8, 0, 3);
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 16) == -ENODATA);
    memset(efuse + 8, 255, 3);
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 16) == -ENODATA);
    data.require_valid_calibration = false; /* Existing other-SoC fallback. */
    efuse[E87N_GT_BIT_OFFSET / 8] = 0;
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, efuse, 16) == 0);
    assert(ctrl.calibration[0] == 19380 && ctrl.calibration[1] == 19380);

    /* Read-only E87N capture on 2026-09-21: nvmem0 at 0x918, length 16.
     * This proves decoding of the observed bytes, not temperature accuracy. */
    u8 observed[16] = {0, 0, 0, 0x3c, 0xb6, 0x4c, 0, 0,
                       0xea, 0x4c, 0, 0, 1, 0x15, 0, 0};
    data.require_valid_calibration = true;
    assert(lvts_calibration_gt(observed, 4, &data, &gt) == 0 && gt == 60);
    assert(lvts_golden_temp_init(&dev, observed, sizeof(observed), &data) == 0);
    assert(golden_temp == 60 && golden_temp_offset == 60 * 500 + 204650);
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, observed, sizeof(observed)) == 0);
    assert(ctrl.calibration[0] == 0x004cb6 && ctrl.calibration[1] == 0x004cea);
    data.gt_calib_bit_offset = 32;
    data.require_valid_calibration = false;
    assert(lvts_calibration_gt(observed, 5, &data, &gt) == 0 && gt == 182);
    data.require_valid_calibration = true;
    assert(lvts_golden_temp_init(&dev, observed, sizeof(observed), &data) == -ENODATA);
    assert(golden_temp == 60 && golden_temp_offset == 60 * 500 + 204650);
    assert(lvts_calibration_init(&dev, &ctrl, &ctrl_data, observed, sizeof(observed)) == -ENODATA);
    data.gt_calib_bit_offset = E87N_GT_BIT_OFFSET;

    u8 registers[32] = {0};
    struct thermal_zone_device zone0 = {.id = 0}, zone2 = {.id = 2};
    ctrl.base = registers;
    ctrl.valid_sensor_mask = 3;
    ctrl.sensors[0].tz = &zone0;
    ctrl.sensors[1].tz = NULL;
    ctrl.sensors[2].tz = &zone2; /* Non-NULL but invalid sensor must be skipped. */
    irq_status = LVTS_INT_SENSOR0 | LVTS_INT_SENSOR1 | LVTS_INT_SENSOR2 | LVTS_INT_SENSOR3;
    assert(lvts_ctrl_irq_handler(&ctrl) == IRQ_HANDLED);
    assert(updates == 1 && irq_acked == irq_status);
    updates = irq_acked = 0;
    irq_status = LVTS_INT_SENSOR1;
    assert(lvts_ctrl_irq_handler(&ctrl) == IRQ_HANDLED);
    assert(updates == 0 && irq_acked == irq_status);
    irq_status = 0;
    assert(lvts_ctrl_irq_handler(&ctrl) == IRQ_NONE && irq_acked == 0);
    printf("PASS: %u bit/length combinations; patch833 offset24/observed eFuse, both real calibration callers, strict errors, unaligned data, IRQ ACK/NULL/invalid sensor checks\n", checks);
    return 0;
}
'''


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory(prefix="e87n-lvts-c-test-") as scratch:
        root = Path(scratch)
        target = root / SOURCE
        target.parent.mkdir(parents=True)
        shutil.copyfile(Path(sys.argv[1]) / SOURCE, target)
        for prefix in ("831", "832", "833"):
            patches = list((REPO / "userpatches/kernel/edgepi-e87n-6.18").glob(prefix + "-*.patch"))
            assert len(patches) == 1
            subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patches[0])],
                           cwd=root, check=True)
        source = target.read_text()
        table = re.search(r"^static const struct lvts_data mt7987_lvts_ap_data = \{(.*?)^\};",
                          source, re.M | re.S)
        assert table, "missing patched MT7987 calibration table"
        offsets = re.findall(r"^\s*\.gt_calib_bit_offset\s*=\s*(\d+)\s*,", table[1], re.M)
        assert offsets == ["24"], "patch 833 must select E87N golden temperature bit offset 24"
        assert re.search(r"^\s*\.require_valid_calibration\s*=\s*true\s*,", table[1], re.M), \
            "patch 833 must retain strict MT7987 calibration validation"
        names = ("lvts_calibration_gt", "lvts_calibration_init", "lvts_golden_temp_init", "lvts_ctrl_irq_handler")
        extracted = "\n\n".join(function(source, name) for name in names)
        harness = root / "lvts-test.c"
        harness.write_text(PRELUDE + "\n#define E87N_GT_BIT_OFFSET " + offsets[0] + "\n" + extracted + TESTS)
        binary = root / "lvts-test"
        subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-parameter", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", str(harness), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        print("PASS: actual patched driver functions under ASan/UBSan; hardware calibration/IRQ not simulated")


if __name__ == "__main__":
    main()
