"""User-launched helper for the current host desktop session."""
from gettext import translation
import os
from pathlib import Path
import select
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep

import gi
from gi.repository import Gio, GLib

APP_ID = "io.github.ivanniks_sudo.SaveDesktopPlus"
NEMO_BUS = "org.NemoDesktop"
ROOT = Path(__file__).resolve().parent
_ = translation("savedesktop", localedir=ROOT / "locale", fallback=True).gettext


def bus_call(bus, method, name):
    return bus.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        method, GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, 1000, None,
    ).unpack()[0]


def owner(bus):
    try:
        return bus_call(bus, "GetNameOwner", NEMO_BUS)
    except GLib.Error as error:
        if Gio.DBusError.get_remote_error(error) == "org.freedesktop.DBus.Error.NameHasNoOwner":
            return None
        raise


def wait_for_owner(bus, previous, seconds):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        current = owner(bus)
        if current and current != previous:
            return current
        sleep(0.1)
    return None


def set_icons(settings, enabled):
    if not settings.set_boolean("show-desktop-icons", enabled):
        raise RuntimeError("Nemo desktop visibility setting was refused")
    Gio.Settings.sync()


def desktop_process_fd(bus, name):
    pid = bus_call(bus, "GetConnectionUnixProcessID", name)
    if Path(f"/proc/{pid}/exe").resolve(strict=True) != Path("/usr/bin/nemo-desktop").resolve(strict=True):
        raise RuntimeError("The desktop bus owner is not nemo-desktop")
    return os.pidfd_open(pid)


def refresh_nemo(pending):
    from nemo_session_restore import NEMO_FILE, _apply_pending, pending_lock

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    schema = Gio.SettingsSchemaSource.get_default().lookup("org.nemo.desktop", True)
    if schema is None or not schema.has_key("show-desktop-icons"):
        raise RuntimeError("Nemo desktop settings are unavailable")
    settings = Gio.Settings.new_full(schema, None, None)
    with pending_lock(pending.parent):
        if not pending.is_file():
            return
        if not settings.get_boolean("show-desktop-icons"):
            raise RuntimeError("Nemo desktop icons are disabled")
        previous = owner(bus)
        if not previous:
            raise RuntimeError("The current Nemo desktop process cannot be identified")
        destination = pending.parent.parent / "nemo" / NEMO_FILE
        process = desktop_process_fd(bus, previous)
        try:
            stopped = select.poll()
            stopped.register(process, select.POLLIN)
            if owner(bus) != previous:
                raise RuntimeError("Nemo desktop changed before the refresh")
            set_icons(settings, False)
            try:
                if not stopped.poll(8000) or owner(bus) is not None:
                    raise RuntimeError("The session manager did not stop the old Nemo desktop")
                _apply_pending(pending, destination, consume=False)
                expected = destination.read_bytes()
            finally:
                set_icons(settings, True)
                current = wait_for_owner(bus, previous, 5)
                if not current and owner(bus) is None:
                    # GApplication owns org.NemoDesktop before opening its desktop.
                    subprocess.Popen(("/usr/bin/nemo-desktop",), start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    current = wait_for_owner(bus, previous, 5)
            if not current or destination.read_bytes() != expected:
                raise RuntimeError("The new Nemo desktop or its restored metadata could not be verified")
            pending.unlink()
        finally:
            os.close(process)


def prompt(message, action, fallback):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=message,
    )
    dialog.set_title("SaveDesktop Plus")
    dialog.add_button(fallback, Gtk.ResponseType.CANCEL)
    dialog.add_button(action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    accepted = dialog.run() == Gtk.ResponseType.OK
    dialog.destroy()
    return accepted


def show_result(text):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=text,
    )
    dialog.set_title("SaveDesktop Plus")
    dialog.run()
    dialog.destroy()


def apply_nemo(pending):
    waiting = Gtk.MessageDialog(
        message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.NONE,
        text=_("Applying the Nemo desktop layout…"),
    )
    waiting.connect("delete-event", lambda *_: True)

    def finished(text):
        waiting.response(Gtk.ResponseType.OK)
        show_result(text)
        return False

    def worker():
        try:
            refresh_nemo(pending)
        except (OSError, RuntimeError, ValueError, GLib.Error) as error:
            print(f"Nemo desktop refresh: {error}", file=sys.stderr)
            text = _("The Nemo desktop refresh could not be verified. Its layout remains queued for the next sign-in.")
        else:
            text = _("The Nemo desktop layout was applied.")
        GLib.idle_add(finished, text)

    Thread(target=worker, daemon=True).start()
    waiting.run()
    waiting.destroy()


def main():
    global Gtk
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    pending = ROOT / "nemo-desktop-pending.json"
    flatpak_path = ROOT / "flatpak-workspace"
    installer = None
    if flatpak_path.is_file():
        workspace = Path(flatpak_path.read_text(encoding="utf-8"))
        candidate = workspace / "flatpaks_installer.py"
        if candidate.is_file() and not (workspace / "flatpak-succeeded").exists():
            installer = candidate
    if pending.is_file():
        if prompt(
            _("Applying the settings restarts the desktop. Make sure all copy and move operations have finished."),
            _("Apply now"), _("Apply at next sign-in"),
        ):
            apply_nemo(pending)
    elif installer is None:
        show_result(_("There is no pending SaveDesktop Plus restore."))
    if installer and prompt(
        _("Close the affected Flatpak apps and keep them closed while their data are being restored."),
        _("Restore Flatpak apps now"), _("Later"),
    ):
        subprocess.Popen(("/usr/bin/python3", "-u", str(installer), "--show-progress"), start_new_session=True)


if __name__ == "__main__":
    main()
