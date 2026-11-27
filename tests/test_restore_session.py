import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import session_restore_host as host


class SessionRestore(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.home = Path(root.name)
        self.state = self.home / ".config" / host.APP_ID
        self.state.mkdir(parents=True)
        self.protected = self.home / "protected"
        self.protected.mkdir()
        self.workspace = self.home / ".var/app" / host.APP_ID / "cache/tmp/workspace"
        self.workspace.mkdir(parents=True)
        self.pending = self.state / "nemo-desktop-pending.json"
        self.preferences = self.workspace / "flatpak-prefs.json"
        self.job = str(uuid4())
        self.pending.write_text(json.dumps({"job": self.job}))
        self.preferences.write_text(json.dumps({"job": self.job, "install-flatpaks": True}))
        self.events = []

    def run_job(self, confirm, *, nemo=True, apps=True):
        with patch.object(host, "HOME", self.home), patch.object(host, "STATE", self.state):
            return host.run_confirmed(
                self.job, nemo, apps, confirm,
                lambda *args: self.events.append(args), self.protected,
            )

    def test_combined_confirmation_and_replay_rejection(self):
        called = []

        def confirm(nemo, prefs):
            called.append((nemo, prefs["install-flatpaks"]))
            return True

        with (patch.object(host, "refresh_nemo") as refresh,
              patch("flatpaks_installer.restore", return_value="success") as restore):
            self.assertEqual(self.run_job(confirm), ("done", "done"))
            with self.assertRaises(ValueError):
                self.run_job(confirm)
            refresh.assert_called_once()
            restore.assert_called_once()
        self.assertEqual(called, [(True, True)])
        self.assertTrue((self.workspace / "flatpak-succeeded").exists())

    def test_declining_keeps_both_jobs_without_running_them(self):
        with (patch.object(host, "refresh_nemo") as refresh,
              patch("flatpaks_installer.restore") as restore):
            self.assertEqual(self.run_job(lambda *_: False), ("deferred", "deferred"))
            refresh.assert_not_called()
            restore.assert_not_called()
        self.assertTrue(self.pending.exists())
        self.assertFalse((self.workspace / "flatpak-succeeded").exists())

    def test_token_must_match_current_payload(self):
        self.pending.write_text(json.dumps({"job": str(uuid4())}))
        with self.assertRaises(ValueError):
            self.run_job(lambda *_: self.fail("stale request prompted for approval"))

    def test_flatpak_payload_is_snapshotted_before_consent(self):
        source = self.workspace / "installed_flatpaks.sh"
        source.write_text("first")

        def confirm(*_):
            source.write_text("changed")
            return True

        def verify(snapshot, home, event):
            self.assertEqual((snapshot / source.name).read_text(), "first")
            return "success"

        with patch("flatpaks_installer.restore", side_effect=verify):
            self.assertEqual(self.run_job(confirm, nemo=False), ("none", "done"))
