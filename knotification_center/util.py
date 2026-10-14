'''Utility and helper functions for KDE Notification Center'''

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtDBus import QDBusConnection, QDBusMessage
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from knotification_center import constants

LOGGER = logging.getLogger(constants.APP_ID)


def setup_logging(level_name: str = "INFO") -> logging.Logger:
    '''Configure application-wide logging format and level'''

    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    LOGGER.setLevel(level)
    return LOGGER


def create_badge_icon(count: int, dnd: bool = False) -> QIcon:
    '''Render desktop notification icon with an optional active count badge'''

    size = 48
    if dnd:
        base_icon = QIcon.fromTheme(
            "notifications-disabled",
            QIcon.fromTheme(
                "preferences-desktop-notification",
                QIcon.fromTheme("dialog-information"),
            ),
        )
    else:
        base_icon = QIcon.fromTheme(
            "preferences-desktop-notification",
            QIcon.fromTheme("dialog-information"),
        )

    base_pixmap = base_icon.pixmap(size, size)
    if base_pixmap.isNull():
        base_pixmap = QPixmap(size, size)
        base_pixmap.fill(Qt.GlobalColor.transparent)

    if count <= 0:
        return QIcon(base_pixmap)

    pixmap = QPixmap(base_pixmap)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    text = str(count) if count < 100 else "99+"
    badge_h = 24
    if len(text) == 1:
        badge_w = 24
    elif len(text) == 2:
        badge_w = 32
    else:
        badge_w = 38

    x = size - badge_w
    y = size - badge_h

    painter.setBrush(QColor("#e74c3c"))
    painter.setPen(QColor("#ffffff"))
    painter.drawRoundedRect(x, y, badge_w, badge_h, badge_h / 2, badge_h / 2)

    # 1.5x larger font (13pt vs previous 9pt) for clear readability
    painter.setFont(QFont("sans-serif", 13, QFont.Weight.Bold))
    painter.drawText(QRect(x, y, badge_w, badge_h), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()

    return QIcon(pixmap)


def generate_badge_png(count: int, dnd: bool = False) -> Path:
    '''Save badge pixmap to disk and return Path for StatusNotifierItem icon'''

    constants.ICONS_DIR.mkdir(parents=True, exist_ok=True)
    prefix = "knotification-center-dnd" if dnd else "knotification-center"
    filepath = constants.ICONS_DIR / f"{prefix}-{count}.png"

    icon = create_badge_icon(count, dnd=dnd)
    pixmap = icon.pixmap(48, 48)
    pixmap.save(str(filepath), "PNG")
    return filepath


def toggle_kde_dnd() -> None:
    '''Invoke native KDE shortcut to toggle Do Not Disturb mode'''

    LOGGER.debug("Invoking KDE shortcut to toggle Do Not Disturb")
    bus = QDBusConnection.sessionBus()
    msg = QDBusMessage.createMethodCall(
        constants.DBUS_KGLOBALACCEL,
        constants.DBUS_KGLOBALACCEL_PATH,
        constants.DBUS_KGLOBALACCEL_IFACE,
        "invokeShortcut",
    )
    msg.setArguments(["toggle do not disturb"])
    bus.call(msg)


def get_dnd_inhibited() -> bool:
    '''Query org.freedesktop.Notifications to check if Do Not Disturb is inhibited'''

    try:
        bus = QDBusConnection.sessionBus()
        msg = QDBusMessage.createMethodCall(
            constants.DBUS_NOTIF_SERVICE,
            constants.DBUS_NOTIF_PATH,
            constants.DBUS_PROPERTIES_IFACE,
            "Get",
        )
        msg.setArguments([constants.DBUS_NOTIF_IFACE, "Inhibited"])
        reply = bus.call(msg)
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
            return bool(reply.arguments()[0])
    except Exception as e:
        LOGGER.debug("Failed querying DND inhibited state: %s", e)
    return False
