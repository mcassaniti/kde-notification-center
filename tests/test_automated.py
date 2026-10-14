'''Automated regression test suite for KDE Notification Center'''

from os import environ
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import dbus
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from gi.repository import GLib
from PyQt6.QtWidgets import QApplication

# Set offscreen QPA for headless execution
environ["QT_QPA_PLATFORM"] = "offscreen"

threads_init()
DBusGMainLoop(set_as_default=True)

from knotification_center import constants
from knotification_center import util
from knotification_center.database import NotificationDB
from knotification_center.dbus_listener import DBusNotificationWorker
from knotification_center.kde_tray import KDEStatusNotifierItem
from knotification_center.main_window import NotificationMainWindow


def pump_events(app: QApplication, duration: float = 0.25) -> None:
    '''Pump Qt and GLib event loops to process asynchronous D-Bus events'''

    ctx = GLib.MainContext.default()
    start = time.time()
    while time.time() - start < duration:
        while ctx.iteration(False):
            pass
        app.processEvents()
        time.sleep(0.01)


def run_tests() -> bool:
    '''Execute automated regression test suite within isolated D-Bus session'''

    temp_dir_str = tempfile.mkdtemp(prefix="knotif_test_")
    temp_dir = Path(temp_dir_str)
    db_path = temp_dir / "test_notifications.sqlite"
    constants.ICONS_DIR = temp_dir / "icons"
    constants.ICONS_DIR.mkdir(parents=True, exist_ok=True)

    util.setup_logging("DEBUG")
    logger = util.LOGGER
    logger.info("Initializing automated test suite in: %s", temp_dir)

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "tests.mock_dbus_server"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout = server_proc.stdout
    assert stdout is not None, "Failed opening stdout of mock server process"
    line = stdout.readline()
    if "MOCK_SERVER_READY" not in line:
        print(f"Failed to start mock D-Bus server: {line}")
        server_proc.kill()
        return False

    q_app = QApplication.instance() or QApplication([sys.argv[0], "-platform", "offscreen"])
    assert isinstance(q_app, QApplication)

    session_bus = dbus.SessionBus()

    notif_obj = session_bus.get_object(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
    )
    notif_iface = dbus.Interface(notif_obj, "org.freedesktop.Notifications")
    watcher_obj = session_bus.get_object(
        "org.kde.StatusNotifierWatcher",
        "/StatusNotifierWatcher",
    )
    watcher_iface = dbus.Interface(watcher_obj, "org.kde.StatusNotifierWatcher")

    def get_test_stats() -> dict[str, Any]:
        raw = notif_iface.GetTestStats()
        return {
            "closed_ids"      : [int(x) for x in raw.get("closed_ids", list())],
            "invoked_actions" : [str(x) for x in raw.get("invoked_actions", list())],
            "replies_received": [str(x) for x in raw.get("replies_received", list())],
        }

    db = NotificationDB(db_path=db_path)
    worker = DBusNotificationWorker(db)
    worker.start_listening()

    window = NotificationMainWindow(db, worker)

    passed = 0
    total = 0

    def assert_test(name: str, condition: bool, details: str = "") -> None:
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name}: {details}")

    print("\n--- Running Automated Regression Tests ---")

    nid1: int = 0

    try:
        # TEST 1: Standard Notification Reception & DB storage
        try:
            nid1 = notif_iface.Notify(
                "Thunderbird",
                0,
                "mail-unread",
                "New Email",
                "You received a new email from Bob.",
                ["read", "Mark as Read", "delete", "Delete"],
                {"urgency": dbus.Byte(1), "category": "email"},
                5000,
            )
            pump_events(q_app, 0.3)
            notifs = db.query_notifications(search_text="Bob")
            assert_test(
                "Test 1: Standard notification reception and DB storage",
                len(notifs) == 1
                and notifs[0]["app_name"] == "Thunderbird"
                and notifs[0]["status"] == constants.Status.ACTIVE.value,
                f"Query result: {notifs}",
            )
        except Exception as e:
            assert_test("Test 1: Standard notification reception", False, str(e))

        # TEST 2: High Urgency & Notification Update (replaces_id)
        try:
            _nid2 = notif_iface.Notify(
                "Thunderbird",
                nid1,
                "mail-unread",
                "Updated Email",
                "Subject edited by Bob.",
                ["read", "Mark as Read"],
                {"urgency": dbus.Byte(2)},
                5000,
            )
            pump_events(q_app, 0.3)
            notifs = db.query_notifications(urgency_filter=int(constants.Urgency.CRITICAL))
            assert_test(
                "Test 2: High urgency notification with replaces_id",
                len(notifs) >= 1 and notifs[0]["urgency"] == int(constants.Urgency.CRITICAL),
                f"Query result: {notifs}",
            )
        except Exception as e:
            assert_test("Test 2: High urgency and replaces_id", False, str(e))

        # TEST 3: Action Button Click (e.g. Thunderbird 'Mark as Read')
        try:
            active_notifs = db.query_notifications(
                status_filter=constants.Status.ACTIVE.value
            )
            t_notif = next((n for n in active_notifs if n["app_name"] == "Thunderbird"), None)
            assert t_notif is not None, "Thunderbird notification not found"

            internal_id = t_notif["id"]
            dbus_id = t_notif.get("dbus_id") or int(nid1)

            window._on_action_invoked(dbus_id, "read", internal_id)
            pump_events(q_app, 0.3)

            stats = get_test_stats()
            action_invoked = f"{dbus_id}:read" in stats["invoked_actions"]
            closed_called = dbus_id in stats["closed_ids"]
            row = db.query_notifications(search_text="Updated Email", status_filter="all")[0]

            assert_test(
                "Test 3: Interactive action button invokes D-Bus action and dismisses",
                action_invoked
                and closed_called
                and row["status"] == constants.Status.DISMISSED.value,
                f"action_invoked={action_invoked}, closed={closed_called}, db_status={row['status']}",
            )
        except Exception as e:
            assert_test("Test 3: Interactive action button", False, str(e))

        # TEST 4: Inline Quick Reply (e.g. Matrix chat / KDE Connect reply)
        try:
            matrix_id = notif_iface.Notify(
                "Matrix",
                0,
                "dialog-information",
                "Alice",
                "Are you available for a quick chat?",
                ["inline-reply", "Reply"],
                {"x-kde-reply-placeholder-text": "Reply to Alice...", "urgency": dbus.Byte(1)},
                -1,
            )
            pump_events(q_app, 0.3)
            matrix_notif = db.query_notifications(
                app_filter="Matrix",
                status_filter=constants.Status.ACTIVE.value,
            )[0]

            has_reply, act_key, _placeholder, _submit_text, _submit_icon = (
                window._check_reply_action(
                    matrix_notif["actions"],
                    matrix_notif["hints"],
                )
            )
            assert has_reply, "Inline reply capability not detected"

            window._on_send_reply(
                int(matrix_id),
                "I will be there in 5 minutes",
                act_key,
                matrix_notif["id"],
            )
            pump_events(q_app, 0.3)

            stats = get_test_stats()
            expected_reply = f"{int(matrix_id)}:I will be there in 5 minutes"
            reply_captured = expected_reply in stats["replies_received"]
            closed_captured = int(matrix_id) in stats["closed_ids"]
            updated_row = db.query_notifications(app_filter="Matrix", status_filter="all")[0]

            assert_test(
                "Test 4: Inline quick reply emits NotificationReplied and closes notification",
                reply_captured
                and closed_captured
                and updated_row["status"] == constants.Status.DISMISSED.value,
                f"reply_captured={reply_captured}, closed={closed_captured}, status={updated_row['status']}",
            )
        except Exception as e:
            assert_test("Test 4: Inline quick reply", False, str(e))

        # TEST 5: Selection & Click-Off Dismissal (Group header vs Item)
        try:
            _s_id1 = notif_iface.Notify("Slack", 0, "", "Slack msg 1", "Meeting at 3", list(), dict(), 5000)
            _s_id2 = notif_iface.Notify("Slack", 0, "", "Slack msg 2", "Coffee break", list(), dict(), 5000)
            pump_events(q_app, 0.3)
            window.reload_data()

            slack_header = None
            for i in range(window.tree.topLevelItemCount()):
                top = window.tree.topLevelItem(i)
                if top and top.data(0, 0x0100 + 2) == "Slack":
                    slack_header = top
                    break
            assert slack_header is not None, "Slack group header not found in tree"

            window.tree.setCurrentItem(slack_header)
            pump_events(q_app, 0.2)

            slack_active = db.query_notifications(
                app_filter="Slack",
                status_filter=constants.Status.ACTIVE.value,
            )
            assert_test(
                "Test 5a: Selecting group header does NOT dismiss group items",
                len(slack_active) == 2,
                f"Expected 2 active, found: {len(slack_active)}",
            )

            c1_id = slack_active[0]["id"]
            c2_id = slack_active[1]["id"]

            item1 = window._find_tree_item_by_id(c1_id)
            assert item1 is not None, "Child 1 not found in tree"
            window.tree.setCurrentItem(item1)
            pump_events(q_app, 0.2)
            notif1_before = next(
                n for n in db.query_notifications(app_filter="Slack") if n["id"] == c1_id
            )
            assert (
                notif1_before["status"] == constants.Status.ACTIVE.value
            ), "Child 1 should remain active while viewed"

            item2 = window._find_tree_item_by_id(c2_id)
            assert item2 is not None, "Child 2 not found in tree"
            window.tree.setCurrentItem(item2)
            pump_events(q_app, 0.2)

            notif1_after = next(
                n for n in db.query_notifications(app_filter="Slack", status_filter="all")
                if n["id"] == c1_id
            )
            assert_test(
                "Test 5b: Click-off dismissal dismisses previous notification on navigation",
                notif1_after["status"] == constants.Status.DISMISSED.value,
                f"Child 1 status after click-off: {notif1_after['status']}",
            )

            window.tree.clearSelection()
            pump_events(q_app, 0.2)
            notif2_after = next(
                n for n in db.query_notifications(app_filter="Slack", status_filter="all")
                if n["id"] == c2_id
            )
            assert_test(
                "Test 5c: Clearing selection dismisses viewed notification",
                notif2_after["status"] == constants.Status.DISMISSED.value,
                f"Child 2 status after clearing selection: {notif2_after['status']}",
            )
        except Exception as e:
            assert_test("Test 5: Click-off dismissal and grouping", False, str(e))

        # TEST 6: Auto-Purge & Retention (purge old dismissed notifications)
        try:
            with db.conn:
                db.conn.execute(
                    """
                    INSERT INTO notifications (app_name, summary, body, status, created_at)
                    VALUES ('OldApp', 'Old Notification', 'Should be purged', 'dismissed', datetime('now', '-10 days'))
                    """
                )
                db.conn.execute(
                    """
                    INSERT INTO notifications (app_name, summary, body, status, created_at)
                    VALUES ('OldApp', 'Old Active', 'Must be preserved', 'active', datetime('now', '-10 days'))
                    """
                )

            purged_count = db.auto_purge_dismissed(days=7)
            old_dismissed = db.query_notifications(
                search_text="Should be purged",
                status_filter="all",
            )
            old_active = db.query_notifications(
                search_text="Must be preserved",
                status_filter="all",
            )

            assert_test(
                "Test 6: Auto-purge removes old dismissed items while preserving active",
                purged_count >= 1 and len(old_dismissed) == 0 and len(old_active) == 1,
                f"purged={purged_count}, old_dismissed={len(old_dismissed)}, old_active={len(old_active)}",
            )
        except Exception as e:
            assert_test("Test 6: Auto-purge and retention", False, str(e))

        # TEST 7: Clear All Active in KDE
        try:
            c_id1 = notif_iface.Notify("BatchApp", 0, "", "Batch 1", "Text", list(), dict(), 5000)
            c_id2 = notif_iface.Notify("BatchApp", 0, "", "Batch 2", "Text", list(), dict(), 5000)
            pump_events(q_app, 0.3)
            window.reload_data()

            active_before = db.get_counts()["active"]
            assert active_before >= 2, f"Expected at least 2 active, got {active_before}"

            window._on_clear_all_active()
            pump_events(q_app, 0.3)

            stats = get_test_stats()
            active_after = db.get_counts()["active"]
            assert_test(
                "Test 7: Clear all active closes notifications on bus and updates DB",
                active_after == 0
                and int(c_id1) in stats["closed_ids"]
                and int(c_id2) in stats["closed_ids"],
                f"active_after={active_after}, closed_ids={stats['closed_ids']}",
            )
        except Exception as e:
            assert_test("Test 7: Clear all active", False, str(e))

        # TEST 8: Full-Text Search and App Filtering
        try:
            notif_iface.Notify(
                "KDEConnect",
                0,
                "",
                "SMS from Jane",
                "Hello there!",
                list(),
                dict(),
                5000,
            )
            pump_events(q_app, 0.3)

            search_jane = db.query_notifications(search_text="Jane", status_filter="all")
            filter_kde = db.query_notifications(app_filter="KDEConnect", status_filter="all")
            filter_unknown = db.query_notifications(app_filter="NonExistentApp", status_filter="all")

            assert_test(
                "Test 8: Full-text search and application name filtering",
                len(search_jane) == 1 and len(filter_kde) == 1 and len(filter_unknown) == 0,
                f"search_jane={len(search_jane)}, filter_kde={len(filter_kde)}, filter_unknown={len(filter_unknown)}",
            )
        except Exception as e:
            assert_test("Test 8: Search and filtering", False, str(e))

        # TEST 9: StatusNotifierItem & Dynamic Badge Generation
        try:
            tray = KDEStatusNotifierItem(
                on_activate=lambda: None,
                get_menu_callback=lambda: None,
            )
            tray.update_badge(
                3,
                summary_items=[{"app_name": "Test", "summary": "Alert"}],
                dnd=False,
            )
            pump_events(q_app, 0.2)

            registered = [str(x) for x in watcher_iface.GetRegisteredItems()]
            icon_file = constants.ICONS_DIR / "knotification-center-3.png"

            assert_test(
                "Test 9: StatusNotifierItem registration and dynamic badge icon creation",
                len(registered) >= 1 and icon_file.exists() and tray.status == "Active",
                f"registered={registered}, icon_exists={icon_file.exists()}, status={tray.status}",
            )
            tray.cleanup()
        except Exception as e:
            assert_test("Test 9: StatusNotifierItem and badge generation", False, str(e))

        # TEST 10: Single-Instance IPC Commands
        try:
            from os import getpid
            from PyQt6.QtNetwork import QLocalServer, QLocalSocket

            test_sock_name = f"knotif_test_ipc_{getpid()}"
            ipc_server = QLocalServer()
            QLocalServer.removeServer(test_sock_name)
            ipc_server.listen(test_sock_name)

            received_cmds: list[str] = list()

            def on_conn() -> None:
                s = ipc_server.nextPendingConnection()
                if s:
                    s.readyRead.connect(
                        lambda: received_cmds.append(
                            s.readAll().data().decode("utf-8").strip()
                        )
                    )

            ipc_server.newConnection.connect(on_conn)

            client = QLocalSocket()
            client.connectToServer(test_sock_name)
            assert client.waitForConnected(500), "Failed connecting to test IPC socket"
            client.write(b"show\n")
            client.flush()
            client.waitForBytesWritten(500)
            client.disconnectFromServer()

            pump_events(q_app, 0.2)
            ipc_server.close()

            assert_test(
                "Test 10: Single-instance IPC socket command communication",
                "show" in received_cmds,
                f"received_cmds={received_cmds}",
            )
        except Exception as e:
            assert_test("Test 10: Single-instance IPC", False, str(e))

        # TEST 11: Ctrl+Q Window Close Shortcut
        try:
            window.show()
            pump_events(q_app, 0.1)
            assert window.isVisible(), "Window should be visible before close"

            closed_event_fired = False

            def on_closed() -> None:
                nonlocal closed_event_fired
                closed_event_fired = True

            window.window_closed.connect(on_closed)
            window._sc_close.activated.emit()
            pump_events(q_app, 0.1)

            assert_test(
                "Test 11: Ctrl+Q shortcut triggers window close and hide",
                not window.isVisible() and closed_event_fired,
                f"isVisible={window.isVisible()}, closed_event_fired={closed_event_fired}",
            )
        except Exception as e:
            assert_test("Test 11: Ctrl+Q close shortcut", False, str(e))

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=2)
        except Exception:
            server_proc.kill()

        db.close()
        try:
            shutil.rmtree(temp_dir_str)
        except Exception:
            pass

    print(f"\nAutomated Test Summary: {passed}/{total} tests passed.\n")
    return passed == total


def test_regression_suite() -> None:
    '''Pytest entrypoint for the automated regression suite'''

    assert run_tests()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
