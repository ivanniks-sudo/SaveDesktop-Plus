"""Describe successful restores and route Cinnamon's session restart."""
import json
import sys

HOST_PROTOCOL = 2
HOST_BUILD = "4.1.0~preview.9"
STEP = "SDPLUS-STEP "
PREFIX = "SDPLUS-RESTORE "
LIVE_DESKTOP_SECTIONS = ("org/cinnamon/desktop/background", "org/cinnamon/desktop/screensaver")


def affects_cinnamon_shell(dump):
    section = ""
    for line in dump.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line and not line.startswith(("#", ";")):
            if section == "org/cinnamon" or section.startswith("org/cinnamon/"):
                if not any(section == prefix or section.startswith(prefix + "/")
                           for prefix in LIVE_DESKTOP_SECTIONS):
                    return True
    return False


def report_step(stage, state="running"):
    print(STEP + json.dumps([stage, state]), file=sys.stderr, flush=True)


def report_completion(result):
    print(PREFIX + json.dumps(result), file=sys.stderr, flush=True)


def read_completion(stderr):
    for line in reversed(stderr.splitlines()):
        if line.startswith(PREFIX):
            try:
                result = json.loads(line[len(PREFIX):])
            except ValueError:
                continue
            if isinstance(result, dict):
                return result
    return {}


def restart_cinnamon(session_type, completed):
    """Call only the existing SessionManager capability, and only in X11."""
    if session_type != "x11":
        completed(False)
        return

    from gi.repository import Gio, GLib

    def finished(connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            print(f"Cinnamon restart request failed: {error}", file=sys.stderr)
            completed(False)
        else:
            completed(True)

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as error:
        print(f"Session bus is unavailable: {error}", file=sys.stderr)
        completed(False)
        return
    connection.call(
        "org.gnome.SessionManager", "/org/gnome/SessionManager",
        "org.gnome.SessionManager", "RestartCinnamonLauncher", None, None,
        Gio.DBusCallFlags.NO_AUTO_START, 5000, None, finished,
    )
