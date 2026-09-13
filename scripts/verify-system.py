#!/usr/bin/env python3
"""Host-only static audit of the new headless system contract; never chroot.

Use an idle, trusted extracted rootfs and original bootfs layout. Passing this
check is NOT an SSH login test, system boot or physical hardware validation.
"""
import argparse
import ast
import ctypes
import ctypes.util
import importlib.util
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("display_audit", REPO / "scripts/verify-display-fan.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
PACKAGES = """python3 python3-pil fonts-dejavu-core e87n-display openssh-server
ca-certificates iproute2 netplan.io tzdata locales apt systemd-resolved systemd-timesyncd""".split()
PAIRS = {
    "e87nctl": "/usr/bin/e87nctl",
    "network/10-e87n-dhcp.yaml": "/etc/netplan/10-e87n-dhcp.yaml",
    "00-e87n-security.conf": "/etc/ssh/sshd_config.d/00-e87n-security.conf",
    "20-e87n-updates": "/etc/apt/apt.conf.d/20-e87n-updates",
    "systemd/e87n-ssh-keygen.conf": "/etc/systemd/system/ssh.service.d/e87n.conf",
    "systemd/e87n-keygen.conf": "/etc/systemd/system/sshd-keygen.service.d/e87n.conf",
}
for name in ("e87n-display",):
    PAIRS["systemd/" + name + ".service"] = "/usr/lib/systemd/system/" + name + ".service"
for name in ("doctor", "__main__", "hardware", "display", "__init__"):
    PAIRS["e87n/" + name + ".py"] = "/usr/lib/python3/dist-packages/e87n/" + name + ".py"


def password_matches(encoded):
    """Host libcrypt only: verify the public factory password without logging hashes."""
    library = ctypes.util.find_library("crypt")
    if not library:
        raise ValueError("host libcrypt required for factory-password verification")
    crypt = ctypes.CDLL(library).crypt
    crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    crypt.restype = ctypes.c_char_p
    result = crypt(b"doumao", encoded.encode("ascii"))
    return bool(result and not result.startswith(b"*") and result.decode("ascii") == encoded)


def check(root, boot):
    def path(name):
        return audit.rooted(root, name)

    def data(name, empty=False):
        return audit.read_file(path(name), allow_empty=empty)

    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(data("/etc/machine-id", True) == b"", "generic image machine-id is not empty")
    for name in ("/var/lib/dbus/machine-id", "/var/lib/systemd/random-seed",
                 "/root/.not_logged_in_yet", "/var/lib/e87n/provisioned",
                 "/var/lib/e87n/pending.json"):
        require(not os.path.lexists(root / name.lstrip("/")), "unexpected generic-image state: " + name)
    require(not list(path("/etc/ssh").glob("ssh_host_*")), "generic image contains SSH host keys")
    require(not os.path.lexists(boot / "e87n-provision.json"), "generic image contains personal provisioning data")
    shadow = data("/etc/shadow").decode().splitlines()
    roots = [line.split(":") for line in shadow if line.startswith("root:")]
    require(len(roots) == 1 and len(roots[0]) >= 9 and password_matches(roots[0][1]),
            "root factory password does not match the requested profile")
    require(roots[0][2].isdigit() and int(roots[0][2]) > 0 and roots[0][7] == "",
            "root password/account is expired or requires first-login reset")
    passwd = data("/etc/passwd").decode().splitlines()
    require(not any(1000 <= int(line.split(":")[2]) < 65534 for line in passwd),
            "generic image contains a pre-created human account")
    for source, dest in PAIRS.items():
        expected = (REPO / "board-support" / source).read_bytes()
        require(data(dest) == expected, "installed source mismatch: " + dest)
        info = path(dest).stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, "writable/unowned system file: " + dest)
        if dest.endswith(".py"):
            ast.parse(expected, filename=dest)
    require(data("/etc/systemd/system/sshd@.service.d/e87n.conf") ==
            data("/etc/systemd/system/ssh.service.d/e87n.conf"), "inetd SSH missing key generation dependency")
    for name in ("e87n-provision-seed", "e87n-provision-console"):
        require(not os.path.lexists(root / "usr/lib/systemd/system" / (name + ".service")),
                "superseded provisioning gate remains installed")
    for name in ("e87n-display", "ssh", "systemd-networkd", "systemd-resolved", "systemd-timesyncd"):
        links = [root / "etc/systemd/system" / target / (name + ".service")
                 for target in ("multi-user.target.wants", "sysinit.target.wants")]
        require(any(link.is_symlink() and audit.rooted(root, "/" + str(link.relative_to(root))).is_file()
                    for link in links),
                "missing enabled unit: " + name)
    socket = root / "etc/systemd/system/ssh.socket"
    require(socket.is_symlink() and os.readlink(socket) == "/dev/null", "SSH socket must be masked")
    for directory in (root / "etc/systemd/system/getty@.service.d",
                      root / "etc/systemd/system/serial-getty@.service.d"):
        for override in directory.glob("*.conf"):
            require("--autologin" not in override.read_text(), "generic image still auto-logs in on console")
    require("OPENSSHD_REGENERATE_HOST_KEYS=false" in data("/etc/default/armbian-firstrun").decode().splitlines(),
            "late Armbian SSH key replacement remains enabled")
    keygen = data("/usr/lib/systemd/system/sshd-keygen.service").decode()
    require(re.search(r'^ExecStart=/usr/bin/ssh-keygen -A$', keygen, re.M) is not None,
            "SSH host-key generation base unit is missing its command")
    for unit in ("sshd-keygen.service", "ssh.service"):
        for directory in ("etc/systemd/system", "run/systemd/system"):
            require(not os.path.lexists(root / directory / unit),
                    "SSH/keygen base unit is masked or overridden: " + unit)
    # Do not execute dpkg or target package scripts. Inspect the installed database.
    installed = set()
    held = set()
    for paragraph in data("/var/lib/dpkg/status").decode().split("\n\n"):
        fields = dict(re.findall(r"^(Package|Status|Version): (.*)$", paragraph, re.M))
        if fields.get("Package") == "e87n-display":
            require(fields.get("Version") == (REPO / "packaging/e87n-display/VERSION").read_text().strip(),
                    "installed display package version mismatch")
        if fields.get("Status", "").endswith(" ok installed"):
            installed.add(fields.get("Package"))
        if fields.get("Status") == "hold ok installed":
            held.add(fields.get("Package"))
    require(set(PACKAGES) <= installed, "missing installed base/display packages: " + ", ".join(sorted(set(PACKAGES) - installed)))
    require({"linux-image-current-filogic", "linux-dtb-current-filogic"} <= held,
            "experimental image/DTB packages are not held")
    owned = set(data("/var/lib/dpkg/info/e87n-display.list").decode().splitlines())
    require({dest for dest in PAIRS.values() if dest.startswith(("/usr/bin/", "/usr/lib/python3/"))}
            | {"/etc/e87n/display.json", "/etc/modules-load.d/e87n-display.conf",
               "/usr/lib/systemd/system/e87n-display.service"} <= owned,
            "display files are not owned by the installed Debian package")
    require(set(data("/var/lib/dpkg/info/e87n-display.conffiles").decode().splitlines()) ==
            {"/etc/e87n/display.json", "/etc/modules-load.d/e87n-display.conf"},
            "display conffiles are not registered for upgrade preservation")
    require(data("/etc/timezone").strip() == b"Asia/Shanghai", "wrong default timezone")
    require(data("/etc/localtime") == data("/usr/share/zoneinfo/Asia/Shanghai"), "localtime does not match Shanghai")
    locale = data("/etc/default/locale").decode()
    require(re.search(r'^LANG="?zh_CN.UTF-8"?$', locale, re.M) is not None, "wrong default UTF-8 locale")
    require(b"zh_CN.UTF-8 UTF-8" in data("/etc/locale.gen").splitlines(), "Chinese locale not selected for generation")
    archive = path("/usr/lib/locale/locale-archive").stat()
    require(stat.S_ISREG(archive.st_mode) and archive.st_size > 0, "generated locale archive missing")
    sources = []
    for file in [root / "etc/apt/sources.list", *sorted((root / "etc/apt/sources.list.d").glob("*"))]:
        if file.is_file() and (file.name == "sources.list" or file.suffix in (".list", ".sources")):
            sources.append(data("/" + str(file.relative_to(root))).decode())
    sources = "\n".join(sources)
    require("trixie" in sources and "trixie-security" in sources, "Debian stable/security APT sources missing")
    require(not re.search(r'trusted\s*=\s*yes|Trusted:\s*yes|Allow-Insecure:\s*yes', sources, re.I),
            "APT source signature checks bypassed")
    require(os.readlink(root / "etc/resolv.conf") == "/run/systemd/resolve/stub-resolv.conf", "DNS resolver link missing")
    fdtget = shutil.which("fdtget")
    require(fdtget is not None, "host fdtget required")
    dtb = audit.rooted(boot, "/dtb-6.18.51-current-filogic/mediatek/mt7987a-edgepi-e87n.dtb")
    for index in (0, 1):
        result = subprocess.run([fdtget, str(dtb), "/aliases", "ethernet" + str(index)],
                                capture_output=True, text=True, timeout=10, check=False)
        require(result.returncode == 0 and result.stdout.strip() ==
                "/soc/ethernet@15100000/mac@" + str(index), "missing/wrong GMAC alias in actual DTB")
    print("PASS: root password SSH profile, unique first-boot identity, DHCP, Shanghai/zh_CN.UTF-8, signed APT sources and display package (static only).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--bootfs", required=True, type=Path)
    args = parser.parse_args()
    try:
        check(args.rootfs.resolve(strict=True), args.bootfs.resolve(strict=True))
    except (OSError, ValueError, SyntaxError, audit.Invalid) as error:
        parser.exit(1, "FAIL: " + str(error) + "\n")


if __name__ == "__main__":
    main()
