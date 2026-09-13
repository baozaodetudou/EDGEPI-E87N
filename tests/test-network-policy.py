#!/usr/bin/env python3
"""Offline DTS alias application and policy fixtures; no udev or networking.

Run: python3 -B tests/test-network-policy.py
Requires GNU patch (gpatch on macOS); Apple patch is not equivalent.
Applies 901 to DTS source reconstructed from 0000 in a temporary directory.
When dtc is present, also resolves aliases in a reduced source-derived fixture.
Does not build the full board DTB or execute systemd's MAC generator.
"""

import configparser
import fnmatch
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
POLICY_DIR = REPO / "board-support/network"
POLICY_NAME = "10-e87n-mtk-mac.link"
PATCH_DIR = REPO / "userpatches/kernel/edgepi-e87n-6.18"
DTS_PATH = "arch/arm64/boot/dts/mediatek/mt7987a-edgepi-e87n.dts"
ALIAS_PATCH = PATCH_DIR / "901-e87n-ethernet-aliases.patch"

# Actual-image 99-default.link reported by the parent audit, also identical to
# systemd v257 network/99-default.link (non-comment directives).
# https://github.com/systemd/systemd/blob/v257/network/99-default.link
DEFAULT_LINK = """\
[Match]
OriginalName=*
[Link]
NamePolicy=keep kernel database onboard slot path
AlternativeNamesPolicy=database onboard slot path mac
MACAddressPolicy=persistent
"""


def parse_link(contents):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(contents)
    return parser


def fixture_matches(config, device):
    """Only the simple positive Match keys used in these fixtures are modeled."""
    fields = {"Driver": "driver", "Type": "type", "OriginalName": "name"}
    for key, patterns in config["Match"].items():
        if key not in fields or any(p.startswith("!") for p in patterns.split()):
            raise ValueError(f"Unsupported fixture match: {key}={patterns}")
        if not any(fnmatch.fnmatchcase(device[fields[key]], pattern)
                   for pattern in patterns.split()):
            return False
    return True


def select_fixture(layers, device):
    """Layers supplied low to high priority; None represents a masked file."""
    merged = {}
    for layer in layers:
        merged.update(layer)
    for name, config in sorted(merged.items()):
        if config is not None and fixture_matches(config, device):
            return name, config
    return None, None


def added_source(patch_text, path):
    """Extract one all-added file from 0000, validating its original hunk size."""
    marker = f"+++ b/{path}\n"
    if patch_text.count(marker) != 1:
        raise ValueError(f"Expected exactly one added source: {path}")
    fragment = patch_text.split(marker, 1)[1].split("diff --git ", 1)[0]
    lines = fragment.splitlines(keepends=True)
    header = re.fullmatch(r"@@ -0,0 \+1,(\d+) @@\n", lines[0])
    if not header or not all(line.startswith("+") for line in lines[1:]):
        raise ValueError(f"Unsupported source hunk for {path}")
    if len(lines) - 1 != int(header[1]):
        raise ValueError(f"Incorrect source hunk length for {path}")
    return "".join(line[1:] for line in lines[1:])


def find_gnu_patch():
    """Match the builder's patch semantics, including asymmetric hunk context."""
    for name in ("gpatch", "patch"):
        executable = shutil.which(name)
        if not executable:
            continue
        version = subprocess.run([executable, "--version"], text=True,
                                 capture_output=True, check=False)
        if version.returncode == 0 and version.stdout.startswith("GNU patch "):
            print(f"Patch tool: {version.stdout.splitlines()[0]} ({executable})", flush=True)
            return executable
    raise RuntimeError("GNU patch is required: install gpatch on macOS or patch on Linux; "
                       "Apple patch cannot validate GNU hunk matching")


class EthernetAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_patch = (PATCH_DIR / "0000-add-mt7987-e87n-dts.patch").read_text()
        cls.original = added_source(source_patch, DTS_PATH)
        cls.soc = added_source(source_patch, "arch/arm64/boot/dts/mediatek/mt7987.dtsi")
        cls.patch_text = ALIAS_PATCH.read_text()
        patch_tool = find_gnu_patch()
        # Real patch application is limited to disposable reconstructed source.
        with tempfile.TemporaryDirectory(prefix="e87n-network-test-") as temporary:
            target = Path(temporary) / DTS_PATH
            target.parent.mkdir(parents=True)
            target.write_text(cls.original)
            result = subprocess.run(
                [patch_tool, "--batch", "--forward", "--fuzz=0", "-p1",
                 "-i", str(ALIAS_PATCH)],
                cwd=temporary, text=True, capture_output=True, check=False,
            )
            output = result.stdout + result.stderr
            if result.returncode or re.search(r"offset|fuzz", output, re.IGNORECASE):
                raise AssertionError(f"901 must apply with zero offset/fuzz:\n{output}")
            cls.board = target.read_text()

    def test_midfile_hunk_has_symmetric_three_line_context(self):
        # GNU patch requires a hunk with more prefix than suffix context to
        # match EOF. This insertion is mid-file, so keep standard diff -U3.
        header = re.search(r"^@@ -14,6 \+14,8 @@\n", self.patch_text, re.MULTILINE)
        self.assertIsNotNone(header)
        hunk = self.patch_text[header.end():].splitlines()
        changed = [i for i, line in enumerate(hunk) if line.startswith(("+", "-"))]
        self.assertEqual(changed, [3, 4])
        self.assertEqual(hunk[:3], [" ", " \taliases {", " \t\tserial0 = &uart0;"])
        self.assertEqual(hunk[5:], [" \t};", " ", " \tchosen {"])

    def test_patch_only_adds_two_aliases_to_original_0000_context(self):
        self.assertEqual(re.findall(r"^\+\+\+ b/(.+)$", self.patch_text, re.MULTILINE),
                         [DTS_PATH])
        anchor = "\t\tserial0 = &uart0;\n"
        self.assertEqual(self.original.count(anchor), 1)
        expected = self.original.replace(
            anchor, anchor + "\t\tethernet0 = &gmac0;\n\t\tethernet1 = &gmac1;\n")
        self.assertEqual(self.board, expected)

    def test_aliases_target_distinct_real_gmac_nodes(self):
        aliases = re.search(r"\baliases\s*\{([^{}]*)\};", self.board)
        self.assertIsNotNone(aliases)
        pairs = re.findall(r"\b(ethernet\d*)\s*=\s*&([\w]+)\s*;", aliases[1])
        self.assertEqual(pairs, [("ethernet0", "gmac0"), ("ethernet1", "gmac1")])
        self.assertEqual(len({label for _, label in pairs}), 2)
        for index, (_, label) in enumerate(pairs):
            node = re.search(rf"\b{label}:\s*mac@{index}\s*\{{([^{{}}]*)\}};", self.soc)
            self.assertIsNotNone(node, label)
            self.assertRegex(node[1], rf"\breg\s*=\s*<{index}>;")
            self.assertIn('compatible = "mediatek,eth-mac";', node[1])
            enabled = re.search(rf"&{label}\s*\{{([^{{}}]*)\}};", self.board)
            self.assertIsNotNone(enabled, label)
            self.assertIn('status = "okay";', enabled[1])

    def test_no_bare_ethernet_alias_conflict_or_fixed_mac(self):
        self.assertNotRegex(self.board, r"\bethernet\s*=")
        additions = [line[1:] for line in self.patch_text.splitlines()
                     if line.startswith("+") and not line.startswith("+++")]
        self.assertEqual(additions,
                         ["\t\tethernet0 = &gmac0;", "\t\tethernet1 = &gmac1;"])
        self.assertNotRegex("\n".join(additions), r"mac-address|nvmem|bootargs|ethaddr")

    @unittest.skipUnless(shutil.which("dtc"), "dtc unavailable; static alias checks still run")
    def test_dtc_resolves_aliases_to_different_mac_paths(self):
        # Reduced fixture, not the complete MT7987 DTB. Use actual alias and
        # GMAC declarations from the patched board/SoC, not invented labels.
        aliases = re.search(r"\baliases\s*\{[^{}]*\};", self.board)[0]
        nodes = [re.search(rf"\bgmac{i}:\s*mac@{i}\s*\{{[^{{}}]*\}};", self.soc)[0]
                 for i in (0, 1)]
        fixture = """/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    uart0: serial {};
    %s
    ethernet@15100000 {
        reg = <0x15100000 0x80000>;
        #address-cells = <1>;
        #size-cells = <0>;
        %s
    };
};
""" % (aliases, "\n".join(nodes))
        compiled = subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-"],
                                  input=fixture.encode(), capture_output=True, check=True)
        decoded = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", "-"],
                                 input=compiled.stdout, capture_output=True, check=True)
        for i in (0, 1):
            self.assertIn(f'ethernet{i} = "/ethernet@15100000/mac@{i}";',
                          decoded.stdout.decode())


class NetworkPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = parse_link((POLICY_DIR / POLICY_NAME).read_text())
        cls.default = parse_link(DEFAULT_LINK)
        cls.vendor = {POLICY_NAME: cls.policy, "99-default.link": cls.default}

    def test_payload_is_one_declarative_link_file(self):
        # No service, script, .network or .netdev may silently gain control.
        self.assertEqual(sorted(p.name for p in POLICY_DIR.glob("*.link")), [POLICY_NAME])
        self.assertEqual(self.policy.defaults(), {})
        self.assertEqual(set(self.policy.sections()), {"Match", "Link"})
        self.assertEqual(dict(self.policy["Match"]),
                         {"Driver": "mtk_soc_eth", "Type": "ether"})
        self.assertEqual(set(self.policy["Link"]),
                         {"NamePolicy", "AlternativeNamesPolicy", "MACAddressPolicy"})

    def test_persistent_policy_without_baked_address(self):
        self.assertEqual(self.policy["Link"]["MACAddressPolicy"], "persistent")
        for section in self.policy.sections():
            self.assertNotIn("MACAddress", self.policy[section])
            self.assertNotIn("PermanentMACAddress", self.policy[section])
        self.assertNotRegex((POLICY_DIR / POLICY_NAME).read_text(),
                            r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")

    def test_preserves_upstream_naming_and_dhcp_match(self):
        for option in ("NamePolicy", "AlternativeNamesPolicy"):
            self.assertEqual(self.policy["Link"][option], self.default["Link"][option])
        self.assertNotIn("Name", self.policy["Link"])
        for name in ("eth0", "eth1", "end0", "enp1s0"):
            with self.subTest(name=name):
                self.assertTrue(fnmatch.fnmatchcase(name, "e*"))

    def test_board_rule_adds_no_identity_or_mac_behavior_to_image_baseline(self):
        # Prevent describing a different selected filename as a MAC fix.
        baseline = {"99-default.link": self.default}
        self.assertEqual(dict(self.policy["Link"]), dict(self.default["Link"]))
        for name in ("eth0", "eth1"):
            device = dict(name=name, driver="mtk_soc_eth", type="ether")
            _, before = select_fixture([baseline], device)
            _, after = select_fixture([self.vendor], device)
            self.assertEqual(dict(before["Link"]), dict(after["Link"]))
        # No synthetic ID_NET_NAME_* or physical-port identity is provided.
        self.assertNotIn("Property", self.policy["Link"])
        self.assertNotIn("ImportProperty", self.policy["Link"])
        self.assertNotIn("UnsetProperty", self.policy["Link"])

    def test_physical_mtk_selection_is_independent_of_address_and_name(self):
        # addr_assign_type is intentionally not a Match key: systemd's
        # persistent policy decides whether to preserve the existing address.
        for name in ("eth0", "eth1", "end0"):
            for assignment in ("permanent", "random", "userspace", "stolen"):
                with self.subTest(name=name, assignment=assignment):
                    device = dict(name=name, driver="mtk_soc_eth", type="ether",
                                  assignment=assignment)
                    chosen, _ = select_fixture([self.vendor], device)
                    self.assertEqual(chosen, POLICY_NAME)

    def test_unrelated_interfaces_do_not_select_board_policy(self):
        devices = [
            dict(name="eth2", driver="r8152", type="ether"),
            dict(name="eth0", driver="r8169", type="ether"),
            dict(name="wlan0", driver="mt7921e", type="wlan"),
            dict(name="veth0", driver="veth", type="ether"),
            dict(name="eth0.10", driver="802.1Q VLAN Support", type="ether"),
            dict(name="br0", driver="bridge", type="ether"),
            dict(name="lo", driver="", type="loopback"),
            dict(name="eth0", driver="mtk_soc_eth_extra", type="ether"),
            dict(name="wlan0", driver="mtk_soc_eth", type="wlan"),
            dict(name="eth0", driver="", type="ether"),
        ]
        for device in devices:
            with self.subTest(device=device):
                chosen, _ = select_fixture([self.vendor], device)
                self.assertEqual(chosen, "99-default.link")

    def test_lexical_order_across_directories(self):
        device = dict(name="eth0", driver="mtk_soc_eth", type="ether")
        runtime = {"10-netplan-eth0.link": self.default}
        admin = {"99-local.link": self.default}
        chosen, _ = select_fixture([self.vendor, runtime, admin], device)
        self.assertEqual(chosen, POLICY_NAME)
        self.assertLess(POLICY_NAME, "70-default.link")
        # Directory priority does not make a later differently named file win.
        chosen, _ = select_fixture([self.vendor, {"05-admin.link": self.default}], device)
        self.assertEqual(chosen, "05-admin.link")

    def test_same_name_override_and_mask(self):
        device = dict(name="eth0", driver="mtk_soc_eth", type="ether")
        replacement = parse_link(DEFAULT_LINK.replace("persistent", "none"))
        chosen, config = select_fixture([self.vendor, {POLICY_NAME: replacement}], device)
        self.assertEqual(chosen, POLICY_NAME)
        self.assertEqual(config["Link"]["MACAddressPolicy"], "none")
        chosen, _ = select_fixture([self.vendor, {POLICY_NAME: None}], device)
        self.assertEqual(chosen, "99-default.link")


if __name__ == "__main__":
    unittest.main(verbosity=2)
