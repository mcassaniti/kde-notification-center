#!/usr/bin/env python3
'''KDE Notification Center - Main Application Entrypoint'''

import argparse
from pathlib import Path
import signal
import sys
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu

from knotification_center import constants
from knotification_center.database import NotificationDB
from knotification_center.dbus_listener import DBusNotificationWorker
from knotification_center.kde_tray import KDEStatusNotifierItem
from knotification_center.main_window import NotificationMainWindow
from knotification_center import util

LOGGER = util.LOGGER


def try_notify_existing_instance(command: str = "show") -> bool:
    '''Notify running instance via local IPC socket; return True if message delivered'''

    socket = QLocalSocket()
    socket.connectToServer(constants.IPC_SOCKET_NAME)
    if socket.waitForConnected(500):
        socket.write(f"{command}\n".encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True
    return False


def main() -> None:
    '''Main application entrypoint'''

    parser = argparse.ArgumentParser(description=constants.APP_NAME)
    parser.add_argument(
        "--show",
        "--window",
        dest="show",
        action="store_true",
        help="Open the main window on startup",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start silently in background",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )
    parser.add_argument(
        "--db",
        default=str(constants.DEFAULT_DB_PATH),
        help="Path to SQLite notifications database",
    )
    parser.add_argument("--tray", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    log_level = "DEBUG" if args.debug else "INFO"
    util.setup_logging(log_level)

    LOGGER.info(
        "Starting %s v%s (log_level=%s)",
        constants.APP_NAME,
        constants.APP_VERSION,
        log_level,
    )

    app = QApplication(sys.argv)
    app.setApplicationName(constants.APP_NAME)
    app.setOrganizationName(constants.APP_ORG)
    app.setDesktopFileName(constants.APP_ID)

    sig_timer = QTimer()
    sig_timer.start(250)
    sig_timer.timeout.connect(lambda: None)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    if args.background:
        socket = QLocalSocket()
        socket.connectToServer(constants.IPC_SOCKET_NAME)
        if socket.waitForConnected(300):
            LOGGER.info("Existing instance detected; background flag given, exiting silently")
            socket.disconnectFromServer()
            sys.exit(0)
    else:
        if try_notify_existing_instance("show"):
            LOGGER.info("Existing instance signaled to show; exiting")
            sys.exit(0)

    base_icon = util.create_badge_icon(0)
    app.setWindowIcon(base_icon)
    app.setQuitOnLastWindowClosed(False)

    db = NotificationDB(db_path=Path(args.db))
    worker = DBusNotificationWorker(db)
    worker.start_listening()

    window = NotificationMainWindow(db, worker)

    def show_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    def toggle_window() -> None:
        if window.isVisible():
            if window.isMinimized():
                window.showNormal()
                window.raise_()
                window.activateWindow()
            else:
                window.hide()
        else:
            show_window()

    tray_menu = QMenu()
    act_show = QAction("Open Notification Center", tray_menu)
    act_show.triggered.connect(show_window)
    tray_menu.addAction(act_show)

    act_clear_active = QAction("Clear All Active Notifications", tray_menu)
    act_clear_active.triggered.connect(window._on_clear_all_active)
    tray_menu.addAction(act_clear_active)

    act_dnd = QAction("🌙 Toggle Do Not Disturb", tray_menu)
    act_dnd.triggered.connect(util.toggle_kde_dnd)
    tray_menu.addAction(act_dnd)

    tray_menu.addSeparator()

    act_quit = QAction("Quit Notification Center", tray_menu)
    act_quit.triggered.connect(app.quit)
    tray_menu.addAction(act_quit)

    tray = KDEStatusNotifierItem(
        on_activate=toggle_window,
        get_menu_callback=lambda: tray_menu,
        on_secondary_activate=util.toggle_kde_dnd,
    )
    app.aboutToQuit.connect(tray.cleanup)
    app.aboutToQuit.connect(db.close)

    def update_tray_state(counts: Optional[dict[str, int]] = None) -> None:
        if counts is None:
            counts = db.get_counts()
        active = counts.get("active", 0)
        summary_items = db.query_notifications(
            status_filter=constants.Status.ACTIVE.value,
            limit=constants.MAX_TOOLTIP_ITEMS,
        )
        dnd = util.get_dnd_inhibited()

        tray.update_badge(active, summary_items=summary_items, dnd=dnd)
        act_dnd.setText("🌙 Turn Off Do Not Disturb" if dnd else "🌙 Turn On Do Not Disturb")
        app.setWindowIcon(util.create_badge_icon(active, dnd=dnd))

    update_tray_state()

    window.counts_updated.connect(update_tray_state)
    worker.notification_added.connect(lambda _: update_tray_state())
    worker.notification_closed.connect(lambda *_: update_tray_state())
    worker.dnd_changed.connect(lambda _: update_tray_state())

    local_server = QLocalServer()
    QLocalServer.removeServer(constants.IPC_SOCKET_NAME)
    local_server.listen(constants.IPC_SOCKET_NAME)

    def on_ipc_connection() -> None:
        client_sock = local_server.nextPendingConnection()
        if client_sock:
            client_sock.readyRead.connect(lambda: _handle_ipc(client_sock))

    def _handle_ipc(sock: QLocalSocket) -> None:
        data = sock.readAll().data().decode("utf-8", errors="ignore").strip()
        LOGGER.debug("Received IPC command: %s", data)
        if "show" in data:
            show_window()
        elif "toggle" in data:
            toggle_window()
        elif "hide" in data:
            window.hide()
        elif "quit" in data:
            app.quit()
        sock.disconnectFromServer()

    local_server.newConnection.connect(on_ipc_connection)

    if args.show:
        show_window()

    exit_code = app.exec()
    local_server.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
