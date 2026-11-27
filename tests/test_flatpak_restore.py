from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flatpaks_installer import desired_apps, restore, restore_lock, PROTECTED_APPS


class FlatpakRestore(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.installed = {"--user": {"org.example.Open", "org.example.Extra", *PROTECTED_APPS},
                          "--system": {"org.example.Ready"}}
        self.active = {"org.example.Open", "org.example.Extra"}
        self.calls = []
        self.offline = False

    def run_flatpak(self, *args, capture=False):
        self.calls.append(args)
        if args[0] == "list":
            return "\n".join(sorted(self.installed[args[1]]))
        if args[0] == "ps":
            return "\n".join(sorted(self.active))
        if args[0] == "install":
            if self.offline and args[-1] == "org.example.Broken":
                raise subprocess.CalledProcessError(1, args)
            self.installed[args[1]].add(args[-1])
        if args[0] == "uninstall":
            self.installed[args[1]].remove(args[-1])

    def prepare(self):
        (self.workspace / "flatpak-prefs.json").write_text(json.dumps({
            "install-flatpaks": True, "copy-data": True,
            "keep-flatpaks": False, "disabled-flatpaks": [],
        }))
        (self.workspace / "installed_flatpaks.sh").write_text(
            "flatpak install --system org.example.Ready -y\n"
            "flatpak install --system org.example.Broken -y\n"
        )
        (self.workspace / "installed_user_flatpaks.sh").write_text(
            "flatpak install --user org.example.Open -y\n"
            "flatpak install --user org.example.Fresh -y\n"
        )
        for app in ("org.example.Fresh", "org.example.Open", "org.example.Broken", *PROTECTED_APPS):
            incoming = self.workspace / "app" / app
            incoming.mkdir(parents=True)
            (incoming / "imported").write_text("backup")
        current = self.home / ".var/app/org.example.Open"
        current.mkdir(parents=True)
        (current / "original").write_text("live")

    def test_recovers_partial_install_and_busy_app_without_replaying_data(self):
        self.prepare()
        self.offline = True
        with patch("flatpaks_installer.run", self.run_flatpak), redirect_stdout(StringIO()) as log:
            self.assertEqual(restore(self.workspace, self.home), "partial")
        self.assertIn("[ERROR] org.example.Broken", log.getvalue())
        self.assertIn("[PENDING] org.example.Open", log.getvalue())
        self.assertNotIn("Finished installing org.example.Broken", log.getvalue())
        data = self.home / ".var/app"
        self.assertFalse((data / "org.example.Open/imported").exists())
        self.assertFalse((data / "org.example.Broken/imported").exists())
        self.assertEqual((data / "org.example.Fresh/imported").read_text(), "backup")
        for app in PROTECTED_APPS:
            self.assertFalse((data / app).exists())
        self.assertIn("org.example.Extra", self.installed["--user"])

        self.active.clear()
        self.offline = False
        (data / "org.example.Fresh/imported").write_text("new session data")
        with patch("flatpaks_installer.run", self.run_flatpak), redirect_stdout(StringIO()):
            self.assertEqual(restore(self.workspace, self.home), "success")
        self.assertEqual((data / "org.example.Fresh/imported").read_text(), "new session data")
        self.assertEqual((data / "org.example.Open/original").read_text(), "live")
        self.assertEqual((data / "org.example.Open/imported").read_text(), "backup")
        self.assertIn("org.example.Broken", self.installed["--system"])
        self.assertNotIn("org.example.Extra", self.installed["--user"])
        self.assertTrue(PROTECTED_APPS.issubset(self.installed["--user"]))
        for scope in ("--user", "--system"):
            self.assertIn(
                ("remote-add", scope, "--if-not-exists",
                 "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"),
                self.calls,
            )

    def test_rejects_unsupported_lines_and_concurrent_restore(self):
        (self.workspace / "installed_flatpaks.sh").write_text(
            "flatpak install --system org.example.App -y; other-command\n"
        )
        with self.assertRaises(ValueError):
            desired_apps(self.workspace, set())
        with restore_lock(self.root), self.assertRaises(BlockingIOError):
            with restore_lock(self.root):
                self.fail("A second run acquired the lock")
