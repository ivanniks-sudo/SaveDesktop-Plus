"""User-launched helper for the current host desktop session."""
from gettext import translation
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep

import gi
from gi.repository import Gio, GLib

APP_ID = "io.github.ivanniks_sudo.SaveDesktopPlus"
NEMO_BUS = "org.NemoDesktop"
ROOT = Path(__file__).resolve().parent
HOME = Path.home()
STATE = HOME / ".config" / APP_ID
SERVICE = APP_ID + ".RestoreHost"
OBJECT = "/" + SERVICE.replace(".", "/")
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
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from nemo_session_restore import NEMO_FILE, _apply_pending, pending_lock

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    schema = Gio.SettingsSchemaSource.get_default().lookup("org.nemo.desktop", True)
    if schema is None or not schema.has_key("show-desktop-icons"):
        raise RuntimeError("Nemo desktop settings are unavailable")
    settings = Gio.Settings.new_full(schema, None, None)
    if any(path.is_symlink() for path in (pending.parent, *pending.parent.parents)):
        raise ValueError("Nemo restore state cannot traverse a symbolic link")
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


def install_host():
    target = HOME / ".local/libexec" / APP_ID
    service_dir = HOME / ".local/share/dbus-1/services"
    if Path(GLib.get_user_data_dir()) != HOME / ".local/share":
        raise RuntimeError("D-Bus activation requires the standard XDG data directory")
    for destination in (target, service_dir):
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise RuntimeError("A symbolic link prevents isolation of the restore service")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("session_restore_host.py", "nemo_session_restore.py", "flatpaks_installer.py", "backup_input.py"):
        temporary = target / (name + ".new")
        shutil.copyfile(ROOT / name, temporary)
        temporary.replace(target / name)
    locale = ROOT / "locale"
    if locale.is_dir():
        shutil.copytree(locale, target / "locale", dirs_exist_ok=True)
    service_dir.mkdir(parents=True, exist_ok=True)
    helper = str(target / "session_restore_host.py")
    service = service_dir / (SERVICE + ".service")
    temp_service = service.with_suffix(".tmp")
    temp_service.write_text(
        f"[D-BUS Service]\nName={SERVICE}\nExec=/usr/bin/python3 -I {GLib.shell_quote(helper)} --dbus\n",
        encoding="utf-8",
    )
    temp_service.replace(service)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "ReloadConfig", None, None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error as error:
        print(f"Refreshing session bus configuration: {error}", file=sys.stderr)
    return helper


def remove_host():
    if not prompt(
        _("Remove the current-session restore service? Pending sign-in restores will be kept."),
        _("Remove the restore service"), _("Cancel"),
    ):
        return
    base = HOME / ".local"
    service = base / "share/dbus-1/services" / (SERVICE + ".service")
    installed = base / "libexec" / APP_ID
    launcher = base / "share/applications" / (SERVICE + ".desktop")
    for destination in (service, installed, launcher):
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise ValueError("The restore service path contains a symbolic link")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if bus_call(bus, "NameHasOwner", SERVICE):
        raise RuntimeError("Close the running restore service before removing it")
    service.unlink(missing_ok=True)
    launcher.unlink(missing_ok=True)
    if installed.is_dir():
        shutil.rmtree(installed)
    show_result(_("The current-session restore service was removed."))


def review():
    pending = STATE / "nemo-desktop-pending.json"
    workspace = HOME / ".var/app" / APP_ID / "cache/tmp/workspace"
    installer = ROOT / "flatpaks_installer.py"
    flatpaks_pending = (workspace / "flatpak-prefs.json").is_file() and not (workspace / "flatpak-succeeded").exists()
    if pending.is_file():
        if prompt(
            _("Applying the settings restarts the desktop. Make sure all copy and move operations have finished."),
            _("Apply now"), _("Apply at next sign-in"),
        ):
            apply_nemo(pending)
    elif not flatpaks_pending:
        show_result(_("There is no pending SaveDesktop Plus restore."))
    if flatpaks_pending and prompt(
        _("Close the affected Flatpak apps and keep them closed while their data are being restored."),
        _("Restore Flatpak apps now"), _("Later"),
    ):
        subprocess.Popen(("/usr/bin/python3", "-u", str(installer), "--current-session", "--show-progress"),
                         start_new_session=True)


def serve():
    xml = f'<node><interface name="{SERVICE}"><method name="ReviewPendingRestore"/></interface></node>'
    interface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
    loop = GLib.MainLoop()
    reviewing = False

    def show():
        try:
            review()
        finally:
            loop.quit()
        return False

    def request(connection, sender, path, iface, method, args, invocation):
        nonlocal reviewing
        invocation.return_value(None)
        if not reviewing:
            reviewing = True
            GLib.idle_add(show)

    def acquired(connection, _name):
        connection.register_object(OBJECT, interface, request, None, None)

    identifier = Gio.bus_own_name(Gio.BusType.SESSION, SERVICE, Gio.BusNameOwnerFlags.NONE,
                                  acquired, None, lambda *_: loop.quit())
    GLib.timeout_add_seconds(30, lambda: False if reviewing else loop.quit())
    loop.run()
    Gio.bus_unown_name(identifier)


def main():
    global Gtk
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if "--bootstrap" in sys.argv[1:]:
        try:
            helper = install_host()
        except (OSError, RuntimeError) as error:
            print(f"Restore service setup: {error}", file=sys.stderr)
        else:
            os.execv("/usr/bin/python3", ("/usr/bin/python3", "-I", helper))
    if "--remove-service" in sys.argv[1:]:
        try:
            remove_host()
        except (OSError, ValueError, RuntimeError, GLib.Error) as error:
            show_result(str(error))
    elif "--dbus" in sys.argv[1:]:
        serve()
    else:
        review()


if __name__ == "__main__":
    main()
