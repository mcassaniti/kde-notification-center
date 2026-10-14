'''Constants for KDE Notification Center'''

from enum import Enum, IntEnum
from pathlib import Path

def _detect_version() -> str:
    '''Detect installed application version, matching systemd-style integer versioning'''

    from os import environ

    if "VERSION" in environ:
        return environ["VERSION"]

    version_file = Path(__file__).parent / "VERSION"
    if version_file.is_file():
        try:
            return version_file.read_text().strip()
        except Exception:
            pass

    try:
        from importlib.metadata import version

        return version("kde-notification-center")
    except Exception:
        pass

    return "001"


APP_NAME    = "KDE Notification Center"
APP_ID      = "knotification-center"
APP_ORG     = "KDE-NotificationCenter"
APP_VERSION = _detect_version()

# Filesystem Paths using pathlib.Path
CONFIG_DIR      = Path.home() / ".config" / "knotification-center"
DATA_DIR        = Path.home() / ".local" / "share" / "knotification_center"
DEFAULT_DB_PATH = DATA_DIR / "notifications.sqlite"
ICONS_DIR       = DATA_DIR / "icons"
IPC_SOCKET_NAME = "knotification-center-ipc"

# Storage & Purging
DEFAULT_RETENTION_DAYS = 30
DEFAULT_PURGE_DAYS     = 7
QUERY_LIMIT_DEFAULT    = 500
MAX_TOOLTIP_ITEMS      = 5

# D-Bus Names, Interfaces and Paths
DBUS_NOTIF_SERVICE      = "org.freedesktop.Notifications"
DBUS_NOTIF_PATH         = "/org/freedesktop/Notifications"
DBUS_NOTIF_IFACE        = "org.freedesktop.Notifications"
DBUS_KDE_NOTIF_MGR      = "org.kde.NotificationManager"
DBUS_PROPERTIES_IFACE   = "org.freedesktop.DBus.Properties"
DBUS_SNI_WATCHER        = "org.kde.StatusNotifierWatcher"
DBUS_SNI_WATCHER_PATH   = "/StatusNotifierWatcher"
DBUS_KGLOBALACCEL       = "org.kde.kglobalaccel"
DBUS_KGLOBALACCEL_PATH  = "/component/plasmashell"
DBUS_KGLOBALACCEL_IFACE = "org.kde.kglobalaccel.Component"

class Urgency(IntEnum):
    '''Urgency levels for desktop notifications'''

    LOW      = 0
    NORMAL   = 1
    CRITICAL = 2

    def __str__(self) -> str:
        labels = {
            Urgency.LOW     : "Low",
            Urgency.NORMAL  : "Normal",
            Urgency.CRITICAL: "Critical",
        }
        return labels.get(self, "Unknown")

class Status(Enum):
    '''Notification lifecycle statuses'''

    ACTIVE    = "active"
    DISMISSED = "dismissed"

    def __str__(self) -> str:
        return self.value


LOG_LEVEL = "INFO"
