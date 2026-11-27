"""Shared contextual help for both views of a restore draft."""

from gi.repository import Adw, Gtk

# Marked for extraction into the application translation catalog.
def N_(message):
    return message


ARTICLES = {
    "archive": N_("Choose a backup made with SaveDesktop Plus. Opening it checks which items are available. It does not change your files or settings."),
    "mode": N_("The wizard walks through the choices in order. Manual mode shows all the choices together. Switching keeps your current choices and uses the same checks."),
    "system_settings": N_("Select this to restore saved system and desktop environment settings. Current settings may be replaced. Leave it off to keep them. Select it when you want the saved desktop environment back."),
    "wallpaper": N_("Select this to restore the wallpaper and how it is displayed. Your current wallpaper will be replaced if the image is available. Leave it off to keep your current wallpaper."),
    "desktop_configuration": N_("Select this to restore icon size, spacing, position and other desktop settings. Desktop files are controlled separately. Finish all copying and moving before applying these settings. Leave it off to keep your current layout."),
    "desktop_contents": N_("Select this to restore files, folders and shortcuts on the desktop. Existing files with different contents are kept. Leave this off if you only want to restore how the desktop looks."),
    "folders": N_("Select this to restore files from the custom folders in the backup. You can choose where each folder goes or skip it. Existing personal files with different contents are kept. Leave this off to keep your folders unchanged."),
    "destination": N_("This is where the files from the backup will go. Use the suggested folder if it belongs to the current account and is where you want the files. Existing personal files with different contents are kept. Select a different folder or clear the check box to skip this folder. If asked, give access in the folder chooser."),
    "flatpak_apps": N_("Select this to reinstall missing Flatpak apps from the backup. Existing apps are kept unless you explicitly allow removal. App data are selected separately. An internet connection and, for system apps, permission to install may be needed."),
    "flatpak_data": N_("Select this to restore saved data such as app preferences. Existing app data are kept in a recovery copy before replacement. Close the affected apps and keep them closed until the restore finishes. Leave this off if you only want to install the apps."),
    "keep_flatpaks": N_("Keep this on to retain your current apps. Turning it off allows removal of Flatpak apps missing from the backup. Their personal data are retained. For example, an editor installed after this backup may be uninstalled. Keeping your apps is recommended unless you want the saved app list."),
    "icons": N_("Select this to restore saved icon themes as part of system settings. Themes with matching names may be updated. Leave this off to retain the themes already present."),
    "themes": N_("Select this to restore saved appearance themes as part of system settings. Themes with matching names may be updated. Leave this off if you want to keep the themes already present."),
    "fonts": N_("Select this to restore the saved fonts. Fonts with matching names may be updated. Leave this off if the current fonts already meet your needs."),
    "bookmarks": N_("Select this to restore saved shortcuts to folders in the file manager. Leave it off to keep your current bookmarks. A shortcut may point to a folder that is not on this computer."),
    "extensions": N_("Select this to restore saved desktop extensions and applets. Existing versions with matching names may be replaced. Leave this off if you want to keep the current extensions."),
    "remember": N_("Keep this on to reuse your component choices and folder destinations. The next restore checks them again. Removing apps always needs a new choice. Turning this off clears the saved restore choices."),
}


def article_text(key):
    return _(ARTICLES[key])


def help_button(parent, key, title):
    label = _("Help: {parameter}").format(parameter=title)
    button = Gtk.Button(icon_name="help-contextual-symbolic", tooltip_text=label,
                        valign=Gtk.Align.CENTER, css_classes=["flat"])
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def show(_button):
        dialog = Adw.AlertDialog.new(title, article_text(key))
        dialog.add_response("close", _("Close help"))
        dialog.set_close_response("close")
        dialog.present(parent)

    button.connect("clicked", show)
    return button
