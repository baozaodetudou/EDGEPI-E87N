#!/usr/bin/env python3
"""Package-only tests. Never install into the host root or start real services.

    python3 -B tests/test-display-package.py

Real archive tests require dpkg/dpkg-deb. Lifecycle tests additionally require
Linux, root and Debian's helpers; their dpkg database lives in a temporary root.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "scripts/build-display-deb.sh"
PACKAGING = REPO / "packaging/e87n-display"
UNIT = "e87n-display.service"
PYTHON_FILES = ("__init__.py", "__main__.py", "hardware.py", "display.py", "doctor.py")
HAVE_DPKG = all(shutil.which(tool) for tool in ("dpkg", "dpkg-deb", "md5sum"))
HAVE_HELPERS = all(shutil.which(tool) for tool in
                   ("deb-systemd-helper", "deb-systemd-invoke", "py3clean"))


def run(argv, *, env=None, cwd=None, check=True):
    result = subprocess.run([str(arg) for arg in argv], env=env, cwd=cwd,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise AssertionError(f"{argv!r} exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


class PortableTests(unittest.TestCase):
    def test_shell_syntax_and_executable_builder(self):
        run(["bash", "-n", BUILD])
        self.assertTrue(os.access(BUILD, os.X_OK))
        for script in ("postinst", "prerm", "postrm"):
            run(["sh", "-n", PACKAGING / script])

    def test_help_needs_no_dpkg(self):
        result = run(["bash", BUILD, "--help"])
        self.assertIn("--output-dir PATH", result.stdout)
        self.assertIn("--version VERSION", result.stdout)

    def test_invalid_arguments_fail_before_writing_output(self):
        with tempfile.TemporaryDirectory(prefix="e87n-package-args-") as temporary:
            output = Path(temporary) / "not-created"
            cases = [[], ["--output-dir"], ["--unknown"], ["--output-dir", ""],
                     ["--output-dir", output, "--version"],
                     ["--output-dir", output, "--output-dir", output],
                     ["--output-dir", output, "--version", "1", "--version", "2"]]
            for version in ("../2", "2/../../bad", "-1", "", "v1.0", "1 2",
                            "1\nDepends: bad", "1;touch marker", "1$(true)"):
                cases.append(["--output-dir", output, "--version", version])
            for args in cases:
                with self.subTest(args=args):
                    result = run(["bash", BUILD, *args], check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())


class MaintainerScriptTests(unittest.TestCase):
    """Run the actual shell scripts with stub commands and a fixture /run path.

    Only the literal /run/systemd/system path is remapped; command logic stays
    unchanged. PATH contains only the stubs, so no host service can be reached.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="e87n-maintscripts-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.runtime = self.root / "running-systemd"
        self.log = self.root / "calls"
        for tool in ("deb-systemd-helper", "deb-systemd-invoke", "systemctl", "py3clean"):
            executable(self.bin / tool, f'''#!/bin/sh
printf '%s\\n' "{tool} $*" >> "$E87N_TEST_LOG"
case "{tool}:$*" in
  'deb-systemd-helper:--quiet was-enabled '*) exit "${{E87N_TEST_DISABLED:-0}}" ;;
  'deb-systemd-helper:enable '*) exit "${{E87N_TEST_ENABLE_FAILURE:-0}}" ;;
  'deb-systemd-invoke:'*) exit "${{E87N_TEST_SERVICE_FAILURE:-0}}" ;;
esac
exit 0
''')
        self.env = {"PATH": str(self.bin), "E87N_TEST_LOG": str(self.log)}

    def invoke(self, script, *args, running=False, **variables):
        if running:
            self.runtime.mkdir(exist_ok=True)
        elif self.runtime.exists():
            self.runtime.rmdir()
        self.log.write_text("")
        source = (PACKAGING / script).read_text()
        fixture = self.root / script
        fixture.write_text(source.replace("/run/systemd/system", str(self.runtime)))
        result = run(["/bin/sh", fixture, *args], env={**self.env, **variables}, check=False)
        return result, self.log.read_text().splitlines()

    def test_image_install_enables_without_runtime_actions(self):
        result, calls = self.invoke("postinst", "configure", "")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [f"deb-systemd-helper unmask {UNIT}",
                                f"deb-systemd-helper --quiet was-enabled {UNIT}",
                                f"deb-systemd-helper enable {UNIT}"])

    def test_live_install_upgrade_and_abort_use_debian_invoke(self):
        for args, action in ((["configure", ""], "start"),
                             (["configure", "1.0.0-1"], "restart"),
                             (["abort-upgrade", "2"], "restart"),
                             (["abort-remove"], "restart"),
                             (["abort-deconfigure"], "restart")):
            with self.subTest(args=args):
                result, calls = self.invoke("postinst", *args, running=True)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(calls[-2:], ["systemctl --system daemon-reload",
                                             f"deb-systemd-invoke {action} {UNIT}"])

    def test_disabled_service_state_is_not_reenabled(self):
        result, calls = self.invoke("postinst", "configure", "1", E87N_TEST_DISABLED="1")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"deb-systemd-helper update-state {UNIT}", calls)
        self.assertNotIn(f"deb-systemd-helper enable {UNIT}", calls)

    def test_service_failure_is_nonfatal_but_enable_failure_is_visible(self):
        result, _ = self.invoke("postinst", "configure", "", running=True,
                                E87N_TEST_SERVICE_FAILURE="1")
        self.assertEqual(result.returncode, 0)
        result, _ = self.invoke("postinst", "configure", "", E87N_TEST_ENABLE_FAILURE="1")
        self.assertNotEqual(result.returncode, 0)

    def test_remove_stops_and_upgrade_defers_restart(self):
        for action in ("remove", "deconfigure", "upgrade"):
            with self.subTest(action=action):
                result, calls = self.invoke("prerm", action, running=True)
                self.assertEqual(result.returncode, 0)
                self.assertIn("py3clean /usr/lib/python3/dist-packages/e87n", calls)
                self.assertEqual(f"deb-systemd-invoke stop {UNIT}" in calls, action != "upgrade")

    def test_dpkg_root_prevents_host_runtime_and_scopes_bytecode_cleanup(self):
        root = str(self.root / "offline")
        for script, args in (("postinst", ["configure", "1"]), ("prerm", ["remove"]),
                             ("postrm", ["remove"]), ("postrm", ["purge"])):
            with self.subTest(script=script, args=args):
                result, calls = self.invoke(script, *args, running=True, DPKG_ROOT=root)
                self.assertEqual(result.returncode, 0)
                self.assertFalse(any(call.startswith(("systemctl ", "deb-systemd-invoke "))
                                     for call in calls))
                if script == "prerm":
                    self.assertIn(f"py3clean {root}/usr/lib/python3/dist-packages/e87n", calls)

    def test_remove_preserves_helper_state_purge_cleans_it_and_upgrade_is_noop(self):
        for action, expected in (("remove", [f"deb-systemd-helper mask {UNIT}"]),
                                 ("purge", [f"deb-systemd-helper purge {UNIT}",
                                            f"deb-systemd-helper unmask {UNIT}"]),
                                 ("upgrade", []), ("failed-upgrade", []),
                                 ("abort-upgrade", []), ("abort-install", [])):
            with self.subTest(action=action):
                result, calls = self.invoke("postrm", action)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(calls, expected)

    def test_postrm_tolerates_missing_dependencies(self):
        for tool in self.bin.iterdir():
            tool.unlink()
        for action in ("remove", "purge"):
            result, calls = self.invoke("postrm", action, running=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(calls, [])


@unittest.skipUnless(HAVE_DPKG, "real package tests require dpkg, dpkg-deb and md5sum")
class DebianPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="e87n-deb-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        cls.staging = cls.work / "overlay with spaces/e87n-package"
        (cls.staging / "scripts").mkdir(parents=True)
        shutil.copy2(BUILD, cls.staging / "scripts/build-display-deb.sh")
        shutil.copytree(PACKAGING, cls.staging / "packaging/e87n-display")
        shutil.copytree(REPO / "board-support", cls.staging / "board-support")
        # Inputs exist even after main removes them; none may enter the archive.
        for name in ("e87n/provision.py", "e87n/future.py", "e87n/__pycache__/display.pyc",
                     "image-defaults.sh", "system-levelpasswordconfig", "docs/unrelated.md"):
            path = cls.staging / "board-support" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not be packaged\n")
        cls.version = (cls.staging / "packaging/e87n-display/VERSION").read_text().strip()
        cls.output = cls.work / "output with spaces"
        cls.script = cls.staging / "scripts/build-display-deb.sh"
        run(["bash", cls.script, "--output-dir", cls.output], cwd=cls.work)
        cls.deb = cls.output / f"e87n-display_{cls.version}_all.deb"
        cls.expected = {}
        for name in PYTHON_FILES:
            cls.expected[f"usr/lib/python3/dist-packages/e87n/{name}"] = (
                cls.staging / "board-support/e87n" / name).read_bytes()
        for source, destination in (
                ("board-support/e87nctl", "usr/bin/e87nctl"),
                ("board-support/display.json", "etc/e87n/display.json"),
                ("board-support/e87n-display.conf", "etc/modules-load.d/e87n-display.conf"),
                ("board-support/systemd/e87n-display.service", f"usr/lib/systemd/system/{UNIT}"),
                ("packaging/e87n-display/copyright", "usr/share/doc/e87n-display/copyright"),
                ("packaging/e87n-display/README.Debian", "usr/share/doc/e87n-display/README.Debian")):
            cls.expected[destination] = (cls.staging / source).read_bytes()
        cls.upgrade_version = cls.version + "+test1"
        (cls.staging / "board-support/display.json").write_text(json.dumps({
            "enabled": True, "brightness_percent": 35, "screen": "thermal", "refresh_seconds": 2,
            "theme": "light", "rotation_enabled": True, "rotation_seconds": 5,
            "rotation_screens": ["thermal", "cpu", "overview"]
        }) + "\n")
        (cls.staging / "board-support/e87n-display.conf").write_text("# new package default\nfb_nv3007\n")
        run(["bash", cls.script, "--output-dir", cls.output, "--version", cls.upgrade_version])
        cls.upgrade = cls.output / f"e87n-display_{cls.upgrade_version}_all.deb"

    def test_minimal_overlay_build_metadata_and_custom_version(self):
        self.assertFalse((self.staging / "docs").exists())
        self.assertFalse((self.staging / "LICENSE").exists())
        for deb, version in ((self.deb, self.version), (self.upgrade, self.upgrade_version)):
            self.assertTrue(deb.is_file())
            for field, expected in (("Package", "e87n-display"), ("Architecture", "all"),
                                    ("Version", version)):
                self.assertEqual(run(["dpkg-deb", "-f", deb, field]).stdout.strip(), expected)
            depends = run(["dpkg-deb", "-f", deb, "Depends"]).stdout.strip()
            self.assertEqual(set(depends.split(", ")), {"python3", "python3-pil", "fonts-dejavu-core",
                                                        "fonts-wqy-microhei",
                                                        "init-system-helpers (>= 1.56)"})

    def test_exact_payload_bytes_ownership_and_permissions(self):
        archive = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(self.deb)],
                                 check=True, stdout=subprocess.PIPE).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            files = {item.name.removeprefix("./"): item for item in tar.getmembers() if item.isfile()}
            self.assertEqual(set(files), set(self.expected))
            for item in tar.getmembers():
                self.assertEqual((item.uid, item.gid), (0, 0), item.name)
                self.assertTrue(item.isfile() or item.isdir(), item.name)
                name = item.name.removeprefix("./")
                self.assertEqual(item.mode, 0o755 if item.isdir() or name == "usr/bin/e87nctl" else 0o644)
            for name, content in self.expected.items():
                self.assertEqual(tar.extractfile(files[name]).read(), content, name)

    def test_control_conffiles_scripts_and_checksums(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            root = Path(temporary)
            control = root / "control"
            payload = root / "payload"
            run(["dpkg-deb", "-e", self.deb, control])
            run(["dpkg-deb", "-x", self.deb, payload])
            self.assertEqual((control / "conffiles").read_text().splitlines(),
                             ["/etc/e87n/display.json", "/etc/modules-load.d/e87n-display.conf"])
            for script in ("postinst", "prerm", "postrm"):
                self.assertEqual((control / script).read_bytes(), (PACKAGING / script).read_bytes())
                self.assertEqual((control / script).stat().st_mode & 0o777, 0o755)
            self.assertFalse((control / "preinst").exists())
            checksums = (control / "md5sums").read_text().splitlines()
            self.assertEqual(len(checksums), len(self.expected))
            for entry in checksums:
                checksum, name = entry.split("  ", 1)
                self.assertEqual(checksum, hashlib.md5((payload / name).read_bytes()).hexdigest())

    def test_missing_whitelisted_source_fails(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            root = Path(temporary) / "overlay"
            shutil.copytree(self.staging, root)
            (root / "board-support/e87n/doctor.py").unlink()
            result = run(["bash", root / "scripts/build-display-deb.sh", "--output-dir", root / "out"],
                         check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing Python source: doctor.py", result.stderr)
            self.assertEqual(list((root / "out").iterdir()), [])

    def test_failed_build_preserves_existing_artifact_and_cleans_stage(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            root = Path(temporary)
            executable(root / "dpkg-deb", "#!/bin/sh\nexit 23\n")
            previous = self.deb.read_bytes()
            result = run(["bash", self.script, "--output-dir", self.output],
                         env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"]}, check=False)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(self.deb.read_bytes(), previous)
            self.assertEqual(list(self.output.glob(".e87n-display.*")), [])

    def test_debian_rejects_incomplete_versions_and_output_symlink(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            root = Path(temporary)
            for version in ("1:", "1-"):
                result = run(["bash", self.script, "--output-dir", root, "--version", version], check=False)
                self.assertNotEqual(result.returncode, 0)
            target = root / "keep"
            target.write_text("untouched")
            (root / self.deb.name).symlink_to(target)
            result = run(["bash", self.script, "--output-dir", root], check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(), "untouched")

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and HAVE_HELPERS,
                         "isolated dpkg lifecycle needs Linux root and Debian helpers")
    def test_real_install_upgrade_remove_reinstall_purge(self):
        for state in ("enabled", "disabled", "masked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory(dir=self.work) as temporary:
                root = Path(temporary) / "root"
                root.mkdir()
                log = Path(temporary) / "runtime-calls"
                fakebin = Path(temporary) / "bin"
                fakebin.mkdir()
                for tool in ("systemctl", "deb-systemd-invoke"):
                    executable(fakebin / tool, '#!/bin/sh\nprintf "%s\\n" "$*" >> "$E87N_TEST_LOG"\nexit 99\n')
                env = {**os.environ, "PATH": str(fakebin) + os.pathsep + os.environ["PATH"],
                       "E87N_TEST_LOG": str(log), "DEBIAN_FRONTEND": "noninteractive"}
                dpkg = ["dpkg", f"--root={root}", "--force-script-chrootless", "--force-depends",
                        "--force-confdef", "--force-confold"]
                run([*dpkg, "--install", self.deb], env=env)
                wants = root / f"etc/systemd/system/multi-user.target.wants/{UNIT}"
                mask = root / f"etc/systemd/system/{UNIT}"
                self.assertTrue(wants.is_symlink())
                if state == "disabled":
                    wants.unlink()  # Same persistent state as systemctl disable.
                elif state == "masked":
                    mask.symlink_to("/dev/null")
                settings = root / "etc/e87n/display.json"
                modules = root / "etc/modules-load.d/e87n-display.conf"
                # Preserve a real 1.2 administrator conffile; the 1.3 loader
                # maps its legacy theme after dpkg correctly keeps the file.
                settings.write_text('{"enabled":false,"brightness_percent":63,"screen":"storage",'
                                    '"refresh_seconds":3,"theme":"compact","rotation_enabled":true,'
                                    '"rotation_seconds":4,"rotation_screens":["storage","thermal"]}\n')
                modules.write_text("# admin module setting\nfb_nv3007\n")
                saved = (settings.read_bytes(), modules.read_bytes())
                unrelated = root / "etc/e87n/local-data"
                unrelated.write_text("keep")
                other_board_data = root / "var/lib/e87n/local-data"
                other_board_data.parent.mkdir(parents=True)
                other_board_data.write_text("keep")
                python = root / "usr/lib/python3/dist-packages/e87n"
                run(["python3", "-m", "compileall", "-q", python])
                self.assertTrue(list(python.rglob("*.pyc")))

                run([*dpkg, "--install", self.upgrade], env=env)
                status = run(["dpkg-query", f"--admindir={root}/var/lib/dpkg", "-W",
                              "-f=${Version} ${Status}", "e87n-display"]).stdout
                self.assertEqual(status, self.upgrade_version + " install ok installed")
                self.assertEqual((settings.read_bytes(), modules.read_bytes()), saved)
                self.assertEqual(wants.is_symlink(), state != "disabled")
                if state == "masked":
                    self.assertEqual(os.readlink(mask), "/dev/null")
                self.assertFalse(list(python.rglob("*.pyc")))

                run([*dpkg, "--remove", "e87n-display"], env=env)
                self.assertEqual((settings.read_bytes(), modules.read_bytes()), saved)
                self.assertFalse((root / "usr/bin/e87nctl").exists())
                self.assertFalse((root / f"usr/lib/systemd/system/{UNIT}").exists())
                run([*dpkg, "--install", self.upgrade], env=env)
                self.assertEqual(wants.is_symlink(), state != "disabled")
                self.assertEqual((settings.read_bytes(), modules.read_bytes()), saved)
                if state == "masked":
                    self.assertEqual(os.readlink(mask), "/dev/null")
                run([*dpkg, "--purge", "e87n-display"], env=env)
                self.assertFalse(settings.exists())
                self.assertFalse(modules.exists())
                self.assertFalse(wants.is_symlink())
                self.assertFalse(list((root / "var/lib/systemd").rglob("e87n-display*")))
                self.assertEqual(unrelated.read_text(), "keep")
                self.assertEqual(other_board_data.read_text(), "keep")
                if state == "masked":
                    self.assertEqual(os.readlink(mask), "/dev/null")
                self.assertFalse(log.exists(), "offline dpkg touched runtime service commands")


@unittest.skipUnless(HAVE_HELPERS, "policy test needs real Debian service helpers")
class PolicyTests(unittest.TestCase):
    def test_real_debian_invoke_respects_policy_101(self):
        with tempfile.TemporaryDirectory(prefix="e87n-policy-") as temporary:
            root = Path(temporary)
            (root / "usr/sbin").mkdir(parents=True)
            executable(root / "usr/sbin/policy-rc.d", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$E87N_POLICY_LOG"\nexit 101\n')
            fakebin = root / "bin"
            fakebin.mkdir()
            executable(fakebin / "systemctl", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$E87N_TEST_LOG"\nexit 99\n')
            runtime_log, policy_log = root / "runtime", root / "policy"
            env = {**os.environ, "DPKG_ROOT": str(root), "E87N_POLICY_LOG": str(policy_log),
                   "E87N_TEST_LOG": str(runtime_log), "PATH": str(fakebin) + os.pathsep + os.environ["PATH"]}
            invoke = shutil.which("deb-systemd-invoke")
            for action in ("start", "restart", "stop"):
                result = run([invoke, action, UNIT], env=env)
                self.assertIn("101", result.stderr)
            self.assertFalse(runtime_log.exists())
            self.assertEqual(policy_log.read_text().splitlines(), [f"{UNIT} {action}" for action in
                                                                  ("start", "restart", "stop")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
