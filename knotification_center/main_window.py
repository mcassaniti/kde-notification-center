'''Main UI window for KDE Notification Center'''

from datetime import datetime
import html
from typing import Any, Callable, Optional

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from knotification_center import constants
from knotification_center.database import NotificationDB
from knotification_center.dbus_listener import DBusNotificationWorker
from knotification_center import util

LOGGER = util.LOGGER


class SortableTreeWidgetItem(QTreeWidgetItem):
    '''Custom QTreeWidgetItem that sorts by Qt.ItemDataRole.UserRole when available'''

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        v1 = self.data(col, Qt.ItemDataRole.UserRole)
        v2 = other.data(col, Qt.ItemDataRole.UserRole)
        if v1 is not None and v2 is not None:
            try:
                return v1 < v2
            except TypeError:
                pass
        return super().__lt__(other)


class NotificationMainWindow(QMainWindow):
    '''Main window providing tree/detail panes, grouping, filtering, and interactive actions'''

    counts_updated = pyqtSignal(dict)
    window_closed = pyqtSignal()

    def __init__(self, db: NotificationDB, worker: DBusNotificationWorker) -> None:
        super().__init__()

        self.db = db
        self.worker = worker
        self.settings = QSettings(constants.APP_ORG, "NotificationCenter")

        self.setWindowTitle(constants.APP_NAME)
        self.setWindowIcon(
            QIcon.fromTheme(
                "preferences-desktop-notification",
                QIcon.fromTheme("dialog-information"),
            )
        )

        self._selected_internal_id: Optional[int] = None
        self._selected_group_app: Optional[str] = None
        self._current_notifications: list[dict[str, Any]] = list()
        self._viewed_active_id: Optional[int] = None
        self._viewed_active_dbus_id: Optional[int] = None

        self._init_ui()
        self._load_settings()
        self._connect_signals()
        self._refresh_apps_dropdown()
        self.reload_data()

    def _init_ui(self) -> None:
        '''Initialize layout, widgets, search/filter bars, and detail inspector'''

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 8)
        main_layout.setSpacing(10)

        # 1. Top Filter & Search Bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search summary, message, or app... (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(260)
        filter_layout.addWidget(self.search_input)

        self.app_combo = QComboBox()
        self.app_combo.addItem("All Applications")
        self.app_combo.setMinimumWidth(160)
        filter_layout.addWidget(self.app_combo)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["Active Only", "All (Active + History)", "Dismissed Only"])
        self.status_combo.setMinimumWidth(150)
        filter_layout.addWidget(self.status_combo)

        self.urgency_combo = QComboBox()
        self.urgency_combo.addItem("All Urgencies", userData=-1)
        self.urgency_combo.addItem(
            "🔴 Critical / Persistent",
            userData=int(constants.Urgency.CRITICAL),
        )
        self.urgency_combo.addItem("🔵 Normal", userData=int(constants.Urgency.NORMAL))
        self.urgency_combo.addItem("⚪ Low", userData=int(constants.Urgency.LOW))
        filter_layout.addWidget(self.urgency_combo)

        self.btn_refresh = QPushButton("↻ Refresh")
        self.btn_refresh.setToolTip("Reload notifications list (Ctrl+R)")
        filter_layout.addWidget(self.btn_refresh)

        main_layout.addLayout(filter_layout)

        # 2. Batch Action Toolbar & Grouping Controls
        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        self.btn_dismiss_selected = QPushButton("✓ Dismiss Selected in KDE")
        self.btn_dismiss_selected.setStyleSheet("QPushButton { font-weight: bold; }")
        self.btn_dismiss_selected.setToolTip(
            "Dismiss selected active notifications in KDE and this view"
        )
        action_layout.addWidget(self.btn_dismiss_selected)

        self.btn_clear_all_active = QPushButton("🧹 Clear All Active in KDE")
        self.btn_clear_all_active.setToolTip(
            "Dismiss all currently active notifications across KDE desktop (no confirmation)"
        )
        action_layout.addWidget(self.btn_clear_all_active)

        self.btn_delete_selected = QPushButton("🗑 Delete from History")
        self.btn_delete_selected.setToolTip(
            "Permanently remove selected notifications from local history database"
        )
        action_layout.addWidget(self.btn_delete_selected)

        self.chk_group_by_app = QCheckBox("Group by Application")
        self.chk_group_by_app.setChecked(True)
        self.chk_group_by_app.setStyleSheet("QCheckBox { font-weight: bold; margin-left: 10px; }")
        self.chk_group_by_app.setToolTip("Group notifications under application folders")
        action_layout.addWidget(self.chk_group_by_app)

        self.btn_expand_all = QToolButton()
        self.btn_expand_all.setText("▼ Expand All")
        self.btn_expand_all.setToolTip("Expand all application groups")
        action_layout.addWidget(self.btn_expand_all)

        self.btn_collapse_all = QToolButton()
        self.btn_collapse_all.setText("▶ Collapse All")
        self.btn_collapse_all.setToolTip("Collapse all application groups")
        action_layout.addWidget(self.btn_collapse_all)

        action_layout.addStretch()
        main_layout.addLayout(action_layout)

        # 3. Main Splitter: Left Tree View | Right Details
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Notification", "Application", "Time", "Urgency", "Status"])
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        header = self.tree.header()
        if header:
            header.setSectionsClickable(True)
            header.setSortIndicatorShown(True)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            header.setSortIndicator(2, Qt.SortOrder.DescendingOrder)

        self.splitter.addWidget(self.tree)

        # Right: Detail Inspector
        self.detail_frame = QFrame()
        self.detail_frame.setFrameShape(QFrame.Shape.StyledPanel)
        detail_layout = QVBoxLayout(self.detail_frame)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(10)

        self.detail_title = QLabel("Select a notification")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.detail_title.setFont(title_font)
        self.detail_title.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)

        self.detail_meta = QLabel("Time: - | Status: -")
        self.detail_meta.setStyleSheet("font-size: 12px; margin-top: 2px; margin-bottom: 2px;")
        detail_layout.addWidget(self.detail_meta)

        self.action_buttons_container = QWidget()
        self.action_buttons_layout = QVBoxLayout(self.action_buttons_container)
        self.action_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.action_buttons_layout.setSpacing(6)
        detail_layout.addWidget(self.action_buttons_container)

        self.detail_body = QTextBrowser()
        self.detail_body.setOpenExternalLinks(True)
        self.detail_body.setStyleSheet(
            "QTextBrowser { border: 1px solid #dcdde1; border-radius: 4px; padding: 8px; font-size: 13px; }"
        )
        detail_layout.addWidget(self.detail_body, stretch=1)

        detail_actions_layout = QHBoxLayout()
        self.btn_detail_copy = QPushButton("Copy Text")
        self.btn_detail_copy.clicked.connect(self._on_detail_copy)
        detail_actions_layout.addWidget(self.btn_detail_copy)

        self.btn_detail_delete = QPushButton("Delete")
        self.btn_detail_delete.clicked.connect(self._on_detail_delete)
        detail_actions_layout.addWidget(self.btn_detail_delete)

        detail_layout.addLayout(detail_actions_layout)

        self.splitter.addWidget(self.detail_frame)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        main_layout.addWidget(self.splitter, stretch=1)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_counts = QLabel("Loading notifications...")
        self.status_bar.addWidget(self.lbl_counts, 1)
        self.lbl_dbus_status = QLabel("● D-Bus: Monitoring Active")
        self.lbl_dbus_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        self.status_bar.addPermanentWidget(self.lbl_dbus_status)

        # Shortcuts
        self._sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self._sc_find.activated.connect(self.search_input.setFocus)

        self._sc_reload = QShortcut(QKeySequence("Ctrl+R"), self)
        self._sc_reload.activated.connect(self.reload_data)

        self._sc_del = QShortcut(QKeySequence("Delete"), self)
        self._sc_del.activated.connect(self._on_delete_shortcut)

        self._sc_esc = QShortcut(QKeySequence("Escape"), self)
        self._sc_esc.activated.connect(self._on_escape)

        self._sc_close = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._sc_close.activated.connect(self.close)

    def _connect_signals(self) -> None:
        '''Connect UI widget events and D-Bus background worker signals'''

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search_timer.timeout.connect(self.reload_data)

        self.search_input.textChanged.connect(lambda: self.search_timer.start())
        self.app_combo.currentIndexChanged.connect(self.reload_data)
        self.status_combo.currentIndexChanged.connect(self.reload_data)
        self.urgency_combo.currentIndexChanged.connect(self.reload_data)
        self.btn_refresh.clicked.connect(self.reload_data)

        self.chk_group_by_app.toggled.connect(self.reload_data)
        self.btn_expand_all.clicked.connect(self.tree.expandAll)
        self.btn_collapse_all.clicked.connect(self.tree.collapseAll)

        self.btn_dismiss_selected.clicked.connect(self._on_dismiss_selected)
        self.btn_clear_all_active.clicked.connect(self._on_clear_all_active)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)

        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)

        self.worker.notification_added.connect(self._on_new_notification_arrived)
        self.worker.notification_closed.connect(self._on_notification_closed_signal)
        self.worker.dbus_id_updated.connect(self._on_dbus_id_updated)

    def _load_settings(self) -> None:
        '''Restore window geometry and grouping preferences from persistent settings'''

        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1150, 700)

        splitter_state = self.settings.value("splitter_state")
        if splitter_state:
            self.splitter.restoreState(splitter_state)

        group_pref = self.settings.value("group_by_app")
        if group_pref is not None:
            self.chk_group_by_app.setChecked(str(group_pref).lower() in ("true", "1"))

    def closeEvent(self, a0: Any) -> None:
        '''Handle window close event: save settings and hide to tray'''

        self._dismiss_viewed_active_if_any()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter_state", self.splitter.saveState())
        self.settings.setValue("group_by_app", self.chk_group_by_app.isChecked())
        if a0:
            a0.ignore()
        self.hide()
        self.window_closed.emit()

    def _on_escape(self) -> None:
        '''Clear search query or tree selection on Escape key'''

        if self.search_input.hasFocus() and self.search_input.text():
            self.search_input.clear()
        else:
            self.tree.clearSelection()

    def _refresh_apps_dropdown(self) -> None:
        '''Refresh application names in filter dropdown'''

        current = self.app_combo.currentText()
        apps = self.db.get_distinct_apps()
        self.app_combo.blockSignals(True)
        self.app_combo.clear()
        self.app_combo.addItem("All Applications")
        for app in apps:
            self.app_combo.addItem(app)
        idx = self.app_combo.findText(current)
        if idx >= 0:
            self.app_combo.setCurrentIndex(idx)
        self.app_combo.blockSignals(False)

    def reload_data(self) -> None:
        '''Reload notification records from database and refresh tree presentation'''

        search_text = self.search_input.text().strip()
        app_filter = self.app_combo.currentText()
        if app_filter in ("All Applications", "All Apps", ""):
            app_filter = ""

        status_text = self.status_combo.currentText()
        if "Active" in status_text and "All" not in status_text:
            status_filter = constants.Status.ACTIVE.value
        elif "Dismissed" in status_text:
            status_filter = constants.Status.DISMISSED.value
        else:
            status_filter = "all"

        urgency_filter = self.urgency_combo.currentData()
        prev_selected_id = self._selected_internal_id
        prev_selected_app = self._selected_group_app

        self.db.auto_purge_dismissed(days=constants.DEFAULT_PURGE_DAYS)

        self._current_notifications = self.db.query_notifications(
            search_text=search_text,
            app_filter=app_filter,
            status_filter=status_filter,
            urgency_filter=urgency_filter,
            limit=constants.QUERY_LIMIT_DEFAULT,
        )

        group_by_app = self.chk_group_by_app.isChecked()
        self.tree.setRootIsDecorated(group_by_app)
        self.btn_expand_all.setEnabled(group_by_app)
        self.btn_collapse_all.setEnabled(group_by_app)

        header = self.tree.header()
        if header:
            sort_col = header.sortIndicatorSection()
            sort_order = header.sortIndicatorOrder()
        else:
            sort_col = 2
            sort_order = Qt.SortOrder.DescendingOrder

        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()

        item_to_reselect = None

        if group_by_app:
            app_groups: dict[str, list[dict[str, Any]]] = dict()
            for n in self._current_notifications:
                app_name = n.get("app_name") or "Unknown"
                app_groups.setdefault(app_name, list()).append(n)

            for app_name, notifs in app_groups.items():
                total = len(notifs)
                active_cnt = sum(
                    1 for n in notifs if n.get("status") == constants.Status.ACTIVE.value
                )
                max_urgency = max(
                    (n.get("urgency", int(constants.Urgency.NORMAL)) for n in notifs),
                    default=int(constants.Urgency.NORMAL),
                )

                latest_ts = 0.0
                latest_time_str = ""
                for n in notifs:
                    c_str = n.get("created_at", "")
                    try:
                        dt = datetime.strptime(c_str, "%Y-%m-%d %H:%M:%S")
                        ts = dt.timestamp()
                        if ts > latest_ts:
                            latest_ts = ts
                            if dt.date() == datetime.now().date():
                                latest_time_str = dt.strftime("%H:%M:%S")
                            else:
                                latest_time_str = dt.strftime("%b %d %H:%M")
                    except Exception:
                        pass

                urg_label = (
                    "🔴 Critical"
                    if max_urgency == int(constants.Urgency.CRITICAL)
                    else (
                        "⚪ Low"
                        if max_urgency == int(constants.Urgency.LOW)
                        else "🔵 Normal"
                    )
                )
                status_summary = f"{active_cnt} Active" if active_cnt > 0 else "All Dismissed"

                parent_item = SortableTreeWidgetItem([
                    f"📁 {app_name} ({total})",
                    app_name,
                    latest_time_str,
                    urg_label,
                    status_summary,
                ])

                parent_item.setData(0, Qt.ItemDataRole.UserRole, app_name.lower())
                parent_item.setData(1, Qt.ItemDataRole.UserRole, app_name.lower())
                parent_item.setData(2, Qt.ItemDataRole.UserRole, latest_ts)
                parent_item.setData(3, Qt.ItemDataRole.UserRole, max_urgency)
                parent_item.setData(4, Qt.ItemDataRole.UserRole, active_cnt)

                parent_item.setData(0, Qt.ItemDataRole.UserRole + 1, None)
                parent_item.setData(0, Qt.ItemDataRole.UserRole + 2, app_name)

                bold_font = parent_item.font(0)
                bold_font.setBold(True)
                for col in range(5):
                    parent_item.setFont(col, bold_font)

                if active_cnt > 0:
                    parent_item.setForeground(4, QColor("#27ae60"))
                else:
                    parent_item.setForeground(4, QColor("#888888"))

                for n in notifs:
                    child_item = self._create_child_tree_item(n, app_name)
                    parent_item.addChild(child_item)
                    if n["id"] == prev_selected_id:
                        item_to_reselect = child_item

                self.tree.addTopLevelItem(parent_item)
                if prev_selected_id is None and prev_selected_app == app_name:
                    item_to_reselect = parent_item

            self.tree.collapseAll()

        else:
            for n in self._current_notifications:
                app_name = n.get("app_name") or "Unknown"
                item = self._create_child_tree_item(n, app_name)
                self.tree.addTopLevelItem(item)
                if n["id"] == prev_selected_id:
                    item_to_reselect = item

        self.tree.setSortingEnabled(True)
        self.tree.sortItems(sort_col, sort_order)
        self.tree.blockSignals(False)

        if item_to_reselect:
            parent = item_to_reselect.parent()
            if parent:
                parent.setExpanded(True)
            self.tree.setCurrentItem(item_to_reselect)
        elif self.tree.topLevelItemCount() == 0:
            self._clear_detail_panel()

        self._update_counts()

    def _create_child_tree_item(
        self,
        n        : dict[str, Any],
        app_name : str,
    ) -> SortableTreeWidgetItem:
        '''Create child notification tree item with sort keys and visual styling'''

        nid = n["id"]
        urgency = n.get("urgency", int(constants.Urgency.NORMAL))
        if urgency == int(constants.Urgency.CRITICAL):
            urgency_text = "🔴 Critical"
        elif urgency == int(constants.Urgency.LOW):
            urgency_text = "⚪ Low"
        else:
            urgency_text = "🔵 Normal"

        created_str = n.get("created_at", "")
        ts_val = 0.0
        try:
            dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
            ts_val = dt.timestamp()
            if dt.date() == datetime.now().date():
                time_display = dt.strftime("%H:%M:%S")
            else:
                time_display = dt.strftime("%b %d %H:%M")
        except Exception:
            time_display = created_str

        summary = n.get("summary", "") or "(No title)"
        status_str = n.get("status", "").capitalize()
        is_active = n.get("status") == constants.Status.ACTIVE.value

        item = SortableTreeWidgetItem([
            summary,
            app_name,
            time_display,
            urgency_text,
            status_str,
        ])

        item.setData(0, Qt.ItemDataRole.UserRole, summary.lower())
        item.setData(1, Qt.ItemDataRole.UserRole, app_name.lower())
        item.setData(2, Qt.ItemDataRole.UserRole, ts_val)
        item.setData(3, Qt.ItemDataRole.UserRole, urgency)
        item.setData(4, Qt.ItemDataRole.UserRole, 1 if is_active else 0)

        item.setData(0, Qt.ItemDataRole.UserRole + 1, nid)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, app_name)

        if is_active:
            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
            item.setForeground(4, QColor("#27ae60"))
        else:
            faded = QColor("#888888")
            for c in range(5):
                item.setForeground(c, faded)

        return item

    def _update_counts(self) -> None:
        '''Update count statistics displayed in status bar'''

        counts = self.db.get_counts()
        showing = len(self._current_notifications)
        self.lbl_counts.setText(
            f"Showing {showing} notifications | Active: {counts['active']} | "
            f"Total Saved: {counts['total']}"
        )
        self.counts_updated.emit(counts)

    def _on_dbus_id_updated(self, internal_id: int, dbus_id: int) -> None:
        '''Update D-Bus ID on in-memory notification when received from method return'''

        for n in self._current_notifications:
            if n["id"] == internal_id:
                n["dbus_id"] = dbus_id
                if self._selected_internal_id == internal_id:
                    self._display_notification_detail(n)
                break

    @staticmethod
    def _check_reply_action(
        actions : list[str],
        hints   : dict[str, Any],
    ) -> tuple[bool, Optional[str], str, str, str]:
        '''Check if notification supports inline reply action'''

        placeholder = hints.get("x-kde-reply-placeholder-text") or "Type a reply…"
        submit_text = hints.get("x-kde-reply-submit-button-text") or "Send"
        submit_icon = hints.get("x-kde-reply-submit-button-icon-name") or "document-send"

        if "x-kde-reply-placeholder-text" in hints or "x-kde-reply-submit-button-text" in hints:
            action_key = "inline-reply"
            if actions and len(actions) >= 2:
                for i in range(0, len(actions) - 1, 2):
                    k = actions[i]
                    lbl = actions[i + 1]
                    if k.lower() in ("inline-reply", "reply") or lbl.lower() == "reply":
                        action_key = k
                        break
            return True, action_key, placeholder, submit_text, submit_icon

        if actions and len(actions) >= 2:
            for i in range(0, len(actions) - 1, 2):
                k = actions[i]
                lbl = actions[i + 1]
                if k.lower() in ("inline-reply", "reply") or lbl.lower() == "reply":
                    return True, k, placeholder, submit_text, submit_icon

        return False, None, placeholder, submit_text, submit_icon

    def _find_tree_item_by_id(self, nid: int) -> Optional[QTreeWidgetItem]:
        '''Find QTreeWidgetItem corresponding to internal notification ID'''

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if not top:
                continue
            if top.data(0, Qt.ItemDataRole.UserRole + 1) == nid:
                return top
            for j in range(top.childCount()):
                child = top.child(j)
                if child and child.data(0, Qt.ItemDataRole.UserRole + 1) == nid:
                    return child
        return None

    def _update_parent_group_active_status(self, item: QTreeWidgetItem) -> None:
        '''Recalculate parent application group active badge status'''

        parent_item = item.parent()
        if parent_item:
            p_app = parent_item.data(0, Qt.ItemDataRole.UserRole + 2)
            group_notifs = [
                n for n in self._current_notifications
                if (n.get("app_name") or "Unknown") == p_app
            ]
            active_cnt = sum(
                1 for n in group_notifs if n.get("status") == constants.Status.ACTIVE.value
            )
            parent_item.setData(4, Qt.ItemDataRole.UserRole, active_cnt)
            status_summary = f"{active_cnt} Active" if active_cnt > 0 else "All Dismissed"
            parent_item.setText(4, status_summary)
            if active_cnt > 0:
                parent_item.setForeground(4, QColor("#27ae60"))
            else:
                parent_item.setForeground(4, QColor("#888888"))

    def _dismiss_viewed_active_if_any(self) -> None:
        '''Dismiss active notification when navigating away from it in the UI'''

        if self._viewed_active_id is not None:
            vid = self._viewed_active_id
            did = self._viewed_active_dbus_id
            self._viewed_active_id = None
            self._viewed_active_dbus_id = None

            LOGGER.debug("Click-off dismissing active notification ID %d (D-Bus ID %s)", vid, did)
            real_did = self.db.mark_dismissed_by_id(vid)
            close_did = real_did or did
            if close_did:
                self.worker.close_notification_on_bus(close_did)

            for n in self._current_notifications:
                if n["id"] == vid:
                    n["status"] = constants.Status.DISMISSED.value
                    break

            item = self._find_tree_item_by_id(vid)
            if item:
                item.setText(4, "Dismissed")
                item.setForeground(4, QColor("#888888"))
                item.setData(4, Qt.ItemDataRole.UserRole, 0)
                self._update_parent_group_active_status(item)

            self.counts_updated.emit(self.db.get_counts())

    def _on_tree_selection_changed(self) -> None:
        '''Handle selection change in notification tree widget'''

        selected_items = self.tree.selectedItems()
        if not selected_items:
            self._dismiss_viewed_active_if_any()
            self._selected_internal_id = None
            self._selected_group_app = None
            self._clear_detail_panel()
            return

        item = selected_items[0]
        nid = item.data(0, Qt.ItemDataRole.UserRole + 1)
        app_name = item.data(0, Qt.ItemDataRole.UserRole + 2)

        if nid is not None:
            if self._viewed_active_id is not None and self._viewed_active_id != nid:
                self._dismiss_viewed_active_if_any()

            self._selected_internal_id = nid
            self._selected_group_app = None
            notif = next((n for n in self._current_notifications if n["id"] == nid), None)
            if notif:
                if notif.get("status") == constants.Status.ACTIVE.value:
                    self._viewed_active_id = nid
                    self._viewed_active_dbus_id = notif.get("dbus_id")
                else:
                    self._viewed_active_id = None
                    self._viewed_active_dbus_id = None

                self._display_notification_detail(notif)
        else:
            self._dismiss_viewed_active_if_any()
            self._selected_internal_id = None
            self._selected_group_app = app_name
            self._display_group_detail(item, app_name)

    def _clear_detail_panel(self) -> None:
        '''Reset inspector panel to empty state'''

        self.detail_title.setText("Select a notification")
        self.detail_meta.setText("Time: - | Status: -")
        self.detail_body.clear()
        self._clear_action_buttons()
        self.btn_detail_copy.setEnabled(False)
        self.btn_detail_delete.setEnabled(False)

    def _clear_action_buttons(self) -> None:
        '''Remove dynamically added action buttons and reply widgets'''

        while self.action_buttons_layout.count():
            child = self.action_buttons_layout.takeAt(0)
            if child:
                w = child.widget()
                if w:
                    w.deleteLater()

    def _display_group_detail(self, group_item: QTreeWidgetItem, app_name: str) -> None:
        '''Display summary information for an entire application group'''

        self._clear_action_buttons()
        group_notifs = [
            n for n in self._current_notifications
            if (n.get("app_name") or "Unknown") == app_name
        ]
        active_cnt = sum(
            1 for n in group_notifs if n.get("status") == constants.Status.ACTIVE.value
        )

        self.detail_title.setText(f"📁 {app_name}")
        active_badge = (
            f"<span style='background-color: #27ae60; color: white; padding: 2px 8px; "
            f"border-radius: 4px; font-weight: bold;'>{active_cnt} Active</span>"
            if active_cnt > 0
            else "<span style='background-color: #576574; color: white; padding: 2px 8px; "
            "border-radius: 4px; font-weight: bold;'>All Dismissed</span>"
        )
        total_badge = (
            f"<span style='background-color: #34495e; color: white; padding: 2px 8px; "
            f"border-radius: 4px; font-weight: bold;'>{len(group_notifs)} Total</span>"
        )
        self.detail_meta.setText(f"{total_badge}  |  {active_badge}")

        html_lines = [
            "<div style='font-family: sans-serif; line-height: 1.5;'>",
            f"<h3>Notifications for {html.escape(app_name)} ({len(group_notifs)} items)</h3>",
            "<ul style='margin-left: -20px;'>",
        ]
        for n in group_notifs:
            status_color = (
                "#27ae60"
                if n.get("status") == constants.Status.ACTIVE.value
                else "#888888"
            )
            summary_txt = html.escape(n.get("summary") or "(No title)")
            time_txt = html.escape(n.get("created_at") or "")
            body_preview = html.escape(n.get("body") or "")[:120]
            if len(n.get("body") or "") > 120:
                body_preview += "..."

            html_lines.append(
                f"<li><b>[{html.escape(n.get('status','').upper())}]</b> "
                f"<span style='color: {status_color}; font-weight: bold;'>{summary_txt}</span> "
                f"<span style='color: #888; font-size: 11px;'>({time_txt})</span><br>"
                f"<span style='color: #444;'>{body_preview}</span></li><br>"
            )
        html_lines.append("</ul></div>")
        self.detail_body.setHtml("".join(html_lines))

        self.btn_detail_copy.setText("Copy Group Summary")
        self.btn_detail_copy.setEnabled(True)
        self.btn_detail_delete.setText(f"Delete All from {app_name}")
        self.btn_detail_delete.setEnabled(True)

    def _display_notification_detail(self, n: dict[str, Any]) -> None:
        '''Render individual notification details, actions, and reply bar'''

        self.btn_detail_copy.setText("Copy Text")
        self.btn_detail_copy.setEnabled(True)
        self.btn_detail_delete.setText("Delete")
        self.btn_detail_delete.setEnabled(True)

        summary = n.get("summary", "") or "(No title)"
        app_name = n.get("app_name", "Unknown")
        created_at = n.get("created_at", "")
        status = n.get("status", "").lower()
        urgency = n.get("urgency", int(constants.Urgency.NORMAL))

        if summary and app_name and app_name.lower() not in summary.lower():
            self.detail_title.setText(f"{app_name}: {summary}")
        else:
            self.detail_title.setText(summary or app_name)

        if urgency == int(constants.Urgency.CRITICAL):
            urg_html = (
                "<span style='background-color: #e74c3c; color: white; padding: 2px 8px; "
                "border-radius: 4px; font-weight: bold;'>🔴 Critical</span>"
            )
        elif urgency == int(constants.Urgency.LOW):
            urg_html = (
                "<span style='background-color: #7f8c8d; color: white; padding: 2px 8px; "
                "border-radius: 4px; font-weight: bold;'>⚪ Low</span>"
            )
        else:
            urg_html = (
                "<span style='background-color: #2980b9; color: white; padding: 2px 8px; "
                "border-radius: 4px; font-weight: bold;'>🔵 Normal</span>"
            )

        if status == constants.Status.ACTIVE.value:
            status_html = (
                "<span style='background-color: #27ae60; color: white; padding: 2px 8px; "
                "border-radius: 4px; font-weight: bold;'>Active</span>"
            )
        else:
            status_html = (
                "<span style='background-color: #576574; color: white; padding: 2px 8px; "
                "border-radius: 4px; font-weight: bold;'>Dismissed</span>"
            )

        dbus_id_txt = f"  |  <b>D-Bus ID:</b> {n.get('dbus_id', 0)}" if n.get("dbus_id") else ""

        self.detail_meta.setText(
            f"<b>Time:</b> {html.escape(created_at)}  |  "
            f"{urg_html}  {status_html}{dbus_id_txt}"
        )

        body = n.get("body", "")
        if body:
            self.detail_body.setHtml(
                f"<div style='font-family: sans-serif; line-height: 1.4;'>{body}</div>"
            )
        else:
            self.detail_body.setHtml("<i style='color: #888;'>No message content</i>")

        self._clear_action_buttons()

        actions = n.get("actions", list())
        hints = n.get("hints", dict())
        dbus_id = n.get("dbus_id", 0)
        nid = n["id"]
        is_active = n.get("status") == constants.Status.ACTIVE.value

        has_reply, reply_act_key, placeholder, submit_text, submit_icon = self._check_reply_action(
            actions, hints
        )

        button_items: list[tuple[str, str]] = list()
        if actions and len(actions) >= 2:
            for i in range(0, len(actions) - 1, 2):
                act_key = actions[i]
                act_label = actions[i + 1]
                if has_reply and act_key == reply_act_key:
                    continue
                if not act_label and act_key == "default":
                    act_label = "Activate"
                if act_label:
                    button_items.append((act_key, act_label))

        buttons_widget = QWidget()
        btn_row_layout = QHBoxLayout(buttons_widget)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        btn_row_layout.setSpacing(6)

        for act_key, act_label in button_items:
            btn = QPushButton(act_label)
            btn.setStyleSheet(
                "QPushButton { padding: 5px 12px; font-weight: bold; border-radius: 4px; }"
                "QPushButton:hover { background-color: #3498db; color: white; }"
            )
            btn.setEnabled(is_active)
            if is_active:
                btn.clicked.connect(
                    lambda checked, k=act_key, did=dbus_id, i_id=nid: self._on_action_invoked(
                        did, k, i_id
                    )
                )
            else:
                btn.setToolTip("Notification is dismissed - actions unavailable")
            btn_row_layout.addWidget(btn)

        if is_active and (button_items or has_reply):
            btn_dismiss = QPushButton("✓ Dismiss")
            btn_dismiss.setStyleSheet(
                "QPushButton { padding: 5px 12px; font-weight: bold; color: #7f8c8d; "
                "border-radius: 4px; }"
                "QPushButton:hover { background-color: #95a5a6; color: white; }"
            )
            btn_dismiss.setToolTip("Dismiss this notification without taking action")
            btn_dismiss.clicked.connect(lambda checked, i_id=nid: self._dismiss_single_notif(i_id))
            btn_row_layout.addWidget(btn_dismiss)

        btn_row_layout.addStretch()

        if button_items or (is_active and has_reply):
            self.action_buttons_layout.addWidget(buttons_widget)

        if has_reply:
            reply_widget = QWidget()
            reply_layout = QHBoxLayout(reply_widget)
            reply_layout.setContentsMargins(0, 4, 0, 4)
            reply_layout.setSpacing(6)

            reply_input = QLineEdit()
            reply_input.setPlaceholderText(placeholder)
            reply_input.setEnabled(is_active)
            reply_input.setStyleSheet(
                "QLineEdit { padding: 6px 10px; border: 1px solid #bdc3c7; border-radius: 4px; font-size: 13px; }"
                "QLineEdit:focus { border: 1px solid #2980b9; }"
            )

            btn_send = QPushButton(f" {submit_text}")
            btn_send.setIcon(
                QIcon.fromTheme(
                    submit_icon,
                    QIcon.fromTheme(
                        "mail-reply-sender",
                        QIcon.fromTheme("document-send"),
                    ),
                )
            )
            btn_send.setStyleSheet(
                "QPushButton { background-color: #2980b9; color: white; font-weight: bold; "
                "padding: 6px 14px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #3498db; }"
                "QPushButton:disabled { background-color: #bdc3c7; color: #7f8c8d; }"
            )
            btn_send.setEnabled(False)

            reply_input.textChanged.connect(
                lambda txt: btn_send.setEnabled(is_active and bool(txt.strip()))
            )

            def _trigger_reply() -> None:
                txt = reply_input.text().strip()
                if not txt:
                    return
                self._on_send_reply(
                    did=dbus_id,
                    reply_text=txt,
                    action_key=reply_act_key,
                    internal_id=nid,
                )

            btn_send.clicked.connect(_trigger_reply)
            reply_input.returnPressed.connect(_trigger_reply)

            reply_layout.addWidget(reply_input, stretch=1)
            reply_layout.addWidget(btn_send)

            self.action_buttons_layout.addWidget(reply_widget)

    def _on_action_invoked(
        self,
        dbus_id     : int,
        action_key  : str,
        internal_id : int,
    ) -> None:
        '''Invoke notification action button and mark dismissed'''

        LOGGER.info(
            "Action button '%s' clicked for notification ID %d (D-Bus ID %d)",
            action_key,
            internal_id,
            dbus_id,
        )
        self._viewed_active_id = None
        self._viewed_active_dbus_id = None
        if dbus_id:
            self.worker.invoke_action_on_bus(dbus_id, action_key)
            self.worker.close_notification_on_bus(dbus_id)
        if internal_id:
            self.db.mark_dismissed_by_id(internal_id)
            self.status_bar.showMessage(
                f"Action '{action_key}' invoked & notification dismissed.",
                3000,
            )
            self.reload_data()

    def _on_send_reply(
        self,
        did         : int,
        reply_text  : str,
        action_key  : Optional[str],
        internal_id : int,
    ) -> None:
        '''Dispatch inline quick-reply and dismiss notification'''

        LOGGER.info("Inline reply submitted for notification ID %d: %r", internal_id, reply_text)
        self._viewed_active_id = None
        self._viewed_active_dbus_id = None
        if did:
            self.worker.send_reply_on_bus(did, reply_text, action_key)
            self.worker.close_notification_on_bus(did)
        if internal_id:
            self.db.mark_dismissed_by_id(internal_id)
            self.status_bar.showMessage("Reply sent & notification dismissed.", 3000)
            self.reload_data()

    def _dismiss_single_notif(self, internal_id: int) -> None:
        '''Dismiss a single notification by internal ID'''

        self._viewed_active_id = None
        self._viewed_active_dbus_id = None
        dbus_id = self.db.mark_dismissed_by_id(internal_id)
        if dbus_id:
            self.worker.close_notification_on_bus(dbus_id)
        self.status_bar.showMessage("Notification dismissed.", 2500)
        self.reload_data()

    def _get_selected_ids(self) -> list[int]:
        '''Collect all internal IDs currently selected in tree'''

        selected_items = self.tree.selectedItems()
        ids = set()
        for item in selected_items:
            if not item:
                continue
            nid = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if nid:
                ids.add(nid)
            for i in range(item.childCount()):
                child = item.child(i)
                if child:
                    child_nid = child.data(0, Qt.ItemDataRole.UserRole + 1)
                    if child_nid:
                        ids.add(child_nid)
        return list(ids)

    def _on_dismiss_selected(self) -> None:
        '''Dismiss all selected notifications in KDE desktop'''

        ids = self._get_selected_ids()
        if not ids:
            return
        LOGGER.debug("Dismissing selected notification IDs: %s", ids)
        dbus_ids = self.db.mark_multiple_dismissed(ids)
        self.worker.close_multiple_on_bus(dbus_ids)
        self.reload_data()

    def _on_clear_all_active(self) -> None:
        '''Dismiss all currently active notifications in KDE without confirmation'''

        LOGGER.info("Clearing all active notifications in KDE")
        dbus_ids = self.db.clear_all_active()
        self.worker.close_multiple_on_bus(dbus_ids)
        self.reload_data()

    def _on_delete_selected(self) -> None:
        '''Permanently delete selected notifications from history database'''

        ids = self._get_selected_ids()
        if not ids:
            return
        LOGGER.debug("Deleting notification IDs: %s", ids)
        active_dbus_ids = self.db.delete_multiple(ids)
        if active_dbus_ids:
            self.worker.close_multiple_on_bus(active_dbus_ids)
        self.reload_data()

    def _on_delete_shortcut(self) -> None:
        '''Delete key shortcut handler'''

        self._on_delete_selected()

    @staticmethod
    def _add_action(
        menu     : QMenu,
        text     : str,
        callback : Callable[[], Any],
        enabled  : bool = True,
    ) -> None:
        '''Helper to safely add an action to a QMenu'''

        act = menu.addAction(text)
        if act:
            act.setEnabled(enabled)
            act.triggered.connect(callback)

    def _on_tree_context_menu(self, pos) -> None:
        '''Construct and display right-click context menu'''

        item = self.tree.itemAt(pos)
        menu = QMenu(self)

        clipboard = QApplication.clipboard()

        if item is not None and item.data(0, Qt.ItemDataRole.UserRole + 1) is None:
            app_name = item.data(0, Qt.ItemDataRole.UserRole + 2) or "Unknown"
            app_notifs = [
                n for n in self._current_notifications
                if (n.get("app_name") or "Unknown") == app_name
            ]
            active_app_notifs = [
                n for n in app_notifs if n.get("status") == constants.Status.ACTIVE.value
            ]

            self._add_action(
                menu,
                f"Dismiss All Active for '{app_name}' ({len(active_app_notifs)})",
                lambda: self._dismiss_app(app_name),
                enabled=len(active_app_notifs) > 0,
            )
            self._add_action(
                menu,
                f"Delete All for '{app_name}' ({len(app_notifs)})",
                lambda: self._delete_app(app_name),
            )

            menu.addSeparator()

            self._add_action(
                menu,
                f"Filter by '{app_name}'",
                lambda: self._filter_by_app(app_name),
            )
            if clipboard:
                self._add_action(
                    menu,
                    "Copy Application Name",
                    lambda: clipboard.setText(app_name),
                )

            menu.addSeparator()

            if item.isExpanded():
                self._add_action(
                    menu,
                    f"Collapse '{app_name}'",
                    lambda: item.setExpanded(False),
                )
            else:
                self._add_action(
                    menu,
                    f"Expand '{app_name}'",
                    lambda: item.setExpanded(True),
                )

            self._add_action(menu, "Expand All", self.tree.expandAll)
            self._add_action(menu, "Collapse All", self.tree.collapseAll)

        elif item is not None:
            selected_ids = self._get_selected_ids()
            nid = item.data(0, Qt.ItemDataRole.UserRole + 1)
            app_name = item.data(0, Qt.ItemDataRole.UserRole + 2) or "Unknown"

            if nid not in selected_ids:
                self.tree.clearSelection()
                item.setSelected(True)
                selected_ids = [nid]

            if len(selected_ids) > 1:
                self._add_action(
                    menu,
                    f"Dismiss Selected ({len(selected_ids)})",
                    self._on_dismiss_selected,
                )
                self._add_action(
                    menu,
                    f"Delete Selected ({len(selected_ids)})",
                    self._on_delete_selected,
                )
                menu.addSeparator()
                self._add_action(
                    menu,
                    f"Copy Selected Titles ({len(selected_ids)})",
                    self._copy_selected_titles,
                )
            else:
                notif = next((n for n in self._current_notifications if n["id"] == nid), None)
                is_active = bool(notif and notif.get("status") == constants.Status.ACTIVE.value)

                self._add_action(
                    menu,
                    "Dismiss Notification",
                    self._on_detail_dismiss,
                    enabled=is_active,
                )
                self._add_action(
                    menu,
                    "Delete from History",
                    self._on_delete_selected,
                )
                menu.addSeparator()

                if clipboard and notif:
                    summary_text = notif.get("summary", "")
                    body_text = notif.get("body", "")
                    self._add_action(
                        menu,
                        "Copy Title",
                        lambda: clipboard.setText(summary_text),
                    )
                    self._add_action(
                        menu,
                        "Copy Message Body",
                        lambda: clipboard.setText(body_text),
                    )
                self._add_action(
                    menu,
                    "Copy Entire Notification Details",
                    self._on_detail_copy,
                )
                menu.addSeparator()
                self._add_action(
                    menu,
                    f"Filter by '{app_name}'",
                    lambda: self._filter_by_app(app_name),
                )

        else:
            if self.chk_group_by_app.isChecked():
                self._add_action(menu, "Expand All", self.tree.expandAll)
                self._add_action(menu, "Collapse All", self.tree.collapseAll)
                menu.addSeparator()

            self._add_action(menu, "Clear All Active in KDE", self._on_clear_all_active)
            self._add_action(menu, "Refresh", self.reload_data)

        viewport = self.tree.viewport()
        if viewport:
            menu.exec(viewport.mapToGlobal(pos))

    def _dismiss_app(self, app_name: str) -> None:
        '''Dismiss all active notifications for a given application'''

        app_notifs = [
            n for n in self._current_notifications
            if (n.get("app_name") or "Unknown") == app_name
            and n.get("status") == constants.Status.ACTIVE.value
        ]
        ids = [n["id"] for n in app_notifs]
        if ids:
            dbus_ids = self.db.mark_multiple_dismissed(ids)
            self.worker.close_multiple_on_bus(dbus_ids)
            self.reload_data()

    def _delete_app(self, app_name: str) -> None:
        '''Permanently delete all notifications for a given application'''

        app_notifs = [
            n for n in self._current_notifications
            if (n.get("app_name") or "Unknown") == app_name
        ]
        ids = [n["id"] for n in app_notifs]
        if ids:
            active_dbus_ids = self.db.delete_multiple(ids)
            if active_dbus_ids:
                self.worker.close_multiple_on_bus(active_dbus_ids)
            self.reload_data()

    def _filter_by_app(self, app_name: str) -> None:
        '''Select specific application in filter dropdown'''

        idx = self.app_combo.findText(app_name)
        if idx >= 0:
            self.app_combo.setCurrentIndex(idx)

    def _copy_selected_titles(self) -> None:
        '''Copy titles of all selected notifications to clipboard'''

        ids = set(self._get_selected_ids())
        titles = [n.get("summary", "") for n in self._current_notifications if n["id"] in ids]
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText("\n".join(titles))

    def _on_detail_dismiss(self) -> None:
        '''Dismiss button handler for currently selected notification or group'''

        if self._selected_internal_id:
            dbus_id = self.db.mark_dismissed_by_id(self._selected_internal_id)
            if dbus_id:
                self.worker.close_notification_on_bus(dbus_id)
            self.reload_data()
        elif self._selected_group_app:
            group_notifs = [
                n for n in self._current_notifications
                if (n.get("app_name") or "Unknown") == self._selected_group_app
                and n.get("status") == constants.Status.ACTIVE.value
            ]
            ids = [n["id"] for n in group_notifs]
            if ids:
                dbus_ids = self.db.mark_multiple_dismissed(ids)
                self.worker.close_multiple_on_bus(dbus_ids)
                self.reload_data()

    def _on_detail_copy(self) -> None:
        '''Copy text of currently selected notification or group summary to clipboard'''

        clipboard = QApplication.clipboard()
        if not clipboard:
            return

        if self._selected_internal_id:
            notif = next(
                (n for n in self._current_notifications if n["id"] == self._selected_internal_id),
                None,
            )
            if notif:
                text = f"{notif.get('summary', '')}\n\n{notif.get('body', '')}".strip()
                clipboard.setText(text)
                self.status_bar.showMessage("Notification copied to clipboard!", 2500)
        elif self._selected_group_app:
            group_notifs = [
                n for n in self._current_notifications
                if (n.get("app_name") or "Unknown") == self._selected_group_app
            ]
            lines = [f"=== {self._selected_group_app} ==="]
            for n in group_notifs:
                lines.append(
                    f"[{n.get('created_at', '')}] {n.get('summary', '')}: {n.get('body', '')}"
                )
            clipboard.setText("\n".join(lines))
            self.status_bar.showMessage("Group summary copied to clipboard!", 2500)

    def _on_detail_delete(self) -> None:
        '''Delete currently selected notification or application group'''

        if self._selected_internal_id:
            dbus_id = self.db.delete_by_id(self._selected_internal_id)
            if dbus_id:
                self.worker.close_notification_on_bus(dbus_id)
            self._selected_internal_id = None
            self.reload_data()
        elif self._selected_group_app:
            group_notifs = [
                n for n in self._current_notifications
                if (n.get("app_name") or "Unknown") == self._selected_group_app
            ]
            ids = [n["id"] for n in group_notifs]
            if ids:
                active_dbus_ids = self.db.delete_multiple(ids)
                if active_dbus_ids:
                    self.worker.close_multiple_on_bus(active_dbus_ids)
                self._selected_group_app = None
                self.reload_data()

    def _on_new_notification_arrived(self, notif_data: dict[str, Any]) -> None:
        '''Handle notification_added signal from D-Bus worker'''

        LOGGER.debug("New notification arrived in UI: %s", notif_data.get("summary"))
        self._refresh_apps_dropdown()
        self.reload_data()

    def _on_notification_closed_signal(self, dbus_id: int, reason: int) -> None:
        '''Handle notification_closed signal from D-Bus worker'''

        LOGGER.debug("Notification closed signal in UI: dbus_id=%d, reason=%d", dbus_id, reason)
        self.reload_data()
