import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import session_restore_host as launcher
from nemo_session_restore import read_keyfile


class NemoLiveRestore(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name)
        self.pending = self.config / "app" / "nemo-desktop-pending.json"
        self.pending.parent.mkdir()
        self.pending.write_text(json.dumps({
            "metadata": "[desktop-monitor-0]\nnemo-icon-view-zoom-level=4",
            "monitors": [(1024, 768)],
        }))
        self.dest = self.config / "nemo" / "desktop-metadata"
        self.dest.parent.mkdir()
        self.dest.write_text("[desktop-monitor-0]\nnemo-icon-view-zoom-level=2\n")

    def simulate(self, *, stops=True, owners=(":1.30",), preserve=False):
        fd = os.open(os.devnull, os.O_RDONLY)
        settings = Mock()
        settings.get_boolean.return_value = True
        poll = Mock()
        poll.poll.return_value = [1] if stops else []
        states = [":1.14", ":1.14"] + ([None] if stops else [])
        if None in owners:
            states.append(None)
        with (
            patch.object(launcher, "desktop_process_fd", return_value=fd),
            patch.object(launcher, "owner", side_effect=states),
            patch.object(launcher, "wait_for_owner", side_effect=owners),
            patch.object(launcher.select, "poll", return_value=poll),
            patch.object(launcher.Gio, "bus_get_sync"),
            patch.object(launcher.Gio.SettingsSchemaSource, "get_default"),
            patch.object(launcher.Gio.Settings, "new_full", return_value=settings),
            patch.object(launcher, "set_icons") as set_icons,
            patch.object(launcher.subprocess, "Popen") as start,
        ):
            if preserve:
                with self.assertRaises(RuntimeError):
                    launcher.refresh_nemo(self.pending)
            else:
                launcher.refresh_nemo(self.pending)
            self.assertEqual([args.args[1] for args in set_icons.call_args_list], [False, True])
            return start.call_count

    def test_uses_session_manager_restart_and_consumes_request(self):
        self.assertEqual(self.simulate(), 0)
        self.assertFalse(self.pending.exists())
        self.assertEqual(read_keyfile(self.dest).get_integer("desktop-monitor-0", "nemo-icon-view-zoom-level"), 4)

    def test_restarts_only_desktop_if_autostart_does_not(self):
        self.assertEqual(self.simulate(owners=(None, ":1.32")), 1)
        self.assertFalse(self.pending.exists())

    def test_retains_queue_if_old_desktop_does_not_stop(self):
        self.assertEqual(self.simulate(stops=False, preserve=True), 0)
        self.assertTrue(self.pending.exists())
        self.assertEqual(read_keyfile(self.dest).get_integer("desktop-monitor-0", "nemo-icon-view-zoom-level"), 2)

    def test_retains_queue_when_new_desktop_is_unconfirmed(self):
        self.assertEqual(self.simulate(owners=(None, None), preserve=True), 1)
        self.assertTrue(self.pending.exists())
