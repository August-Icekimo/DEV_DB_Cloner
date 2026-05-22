"""
tui_screens.py — All Textual TUI screens and App classes.
No DB execution logic here; calls into clone_engine for DDL preview.
"""
import os
import json
import logging
from typing import List, Dict, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Label, Button, Static, TextArea, Input
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.message import Message
from textual.containers import Grid

from config_manager import config_mgr
from clone_engine import fetch_ddl, fetch_dependencies

logger = logging.getLogger("DB_Replicator")


# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------

class ProjectSettingsScreen(ModalScreen[bool]):
    """Modal screen for editing Project Settings (Name Source)"""

    CSS = """
    ProjectSettingsScreen {
        align: center middle;
    }
    #settings-dialog {
        width: 80;
        height: 23;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    .settings-title {
        text-style: bold;
        margin-bottom: 1;
    }
    .field-label {
        margin-top: 1;
        text-style: bold;
    }
    #source-type-tabs {
        height: 3;
        margin: 0 0 1 0;
    }
    #source-type-tabs Button {
        margin-right: 1;
        min-width: 16;
    }
    .tab-active {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    .tab-inactive {
        background: $primary-lighten-3;
        color: $primary;
    }
    #source-value-input {
        margin: 0 0 1 0;
    }
    .help-text {
        color: $text-muted;
        margin-bottom: 1;
    }
    #settings-buttons {
        height: 4;
        align: center middle;
        margin-top: 1;
    }
    #settings-buttons Button {
        margin: 0 2;
        min-width: 14;
    }
    #btn-cancel {
        background: $surface-darken-2;
        color: $text;
    }
    #btn-save {
        background: $success;
        color: $text;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("c", "cancel", "(C)ancel"),
        ("s", "save", "(S)ave"),
    ]

    def __init__(self, project_id: int) -> None:
        super().__init__()
        self.project_id = project_id
        self.project = config_mgr.get_project_by_id(project_id)
        self.current_type = self.project.name_source_type
        self.current_value = self.project.name_source_value or ""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"⚙️ 專案設定: {self.project.name}", classes="settings-title"),
            Label("姓名來源設定 (Name Source):", classes="field-label"),
            Horizontal(
                Button("預設\nDefault", id="type-default"),
                Button("資料庫\nDatabase", id="type-db"),
                Button("檔案\nFile", id="type-file"),
                id="source-type-tabs"
            ),
            Label("來源設定值 (Source Value):", classes="field-label"),
            Label("  DB → Table.Column (e.g. USERS.full_name)  |  File → path/to/names.json", classes="help-text"),
            Input(value=self.current_value, placeholder="輸入來源設定值...", id="source-value-input"),
            Horizontal(
                Button("取消\n(C)ancel", id="btn-cancel"),
                Button("儲存\n(S)ave", id="btn-save"),
                id="settings-buttons"
            ),
            id="settings-dialog"
        )

    def on_mount(self) -> None:
        self._update_tabs()

    def _update_tabs(self):
        type_map = {"DEFAULT": "type-default", "DB": "type-db", "FILE": "type-file"}
        for type_key, btn_id in type_map.items():
            btn = self.query_one(f"#{btn_id}", Button)
            if self.current_type == type_key:
                btn.remove_class("tab-inactive")
                btn.add_class("tab-active")
            else:
                btn.remove_class("tab-active")
                btn.add_class("tab-inactive")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-save":
            self._do_save()
        elif event.button.id.startswith("type-"):
            new_type = event.button.id.split("-")[1].upper()
            self.current_type = new_type
            self._update_tabs()

    def _do_save(self):
        val = self.query_one("#source-value-input", Input).value.strip()
        config_mgr.update_project_settings(
            self.project_id,
            self.project.name,
            self.project.description,
            self.current_type,
            val
        )
        self.notify("✅ 設定已更新")
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)

    def action_save(self):
        self._do_save()


class NewProjectScreen(ModalScreen[str]):
    CSS = """
    NewProjectScreen { align: center middle; }
    #new-proj-dialog { width: 60; height: 8; border: thick $background 80%; background: $surface; padding: 1 2; }
    #new-proj-name { margin: 1 0; }
    #new-proj-buttons { height: 3; align: center middle; }
    #new-proj-buttons Button { margin: 0 1; }
    """

    BINDINGS = [("escape", "cancel", "取消")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("📁 建立新專案 (Enter 確認 / Esc 取消):"),
            Input(placeholder="輸入專案名稱...", id="new-proj-name"),
            Horizontal(
                Button("取消 [Esc]", id="cancel"),
                Button("建立 [Enter]", variant="primary", id="create"),
                id="new-proj-buttons"
            ),
            id="new-proj-dialog"
        )

    def on_mount(self) -> None:
        self.query_one("#new-proj-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(name)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "create":
            name = self.query_one("#new-proj-name", Input).value.strip()
            if name:
                self.dismiss(name)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class InfoScreen(ModalScreen[None]):
    """Modal screen to display Info.txt content"""

    CSS = """
    InfoScreen { align: center middle; }
    #info-dialog { width: 70; height: 30; border: thick $background 80%; background: $surface; padding: 1 2; }
    #info-content { height: 1fr; }
    #info-hint { height: 1; content-align: center middle; color: $text-muted; }
    """

    BINDINGS = [("q", "close", "關閉"), ("escape", "close", "關閉")]

    def compose(self) -> ComposeResult:
        info_text = "(Info.txt 檔案不存在)"
        info_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Info.txt")
        if os.path.exists(info_path):
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    info_text = f.read()
            except Exception as e:
                info_text = f"讀取 Info.txt 失敗: {e}"
        yield Vertical(
            TextArea(info_text, read_only=True, id="info-content"),
            Label("按 Q 或 ESC 關閉", id="info-hint"),
            id="info-dialog"
        )

    def action_close(self) -> None:
        self.dismiss(None)


class ImportPrefixScreen(ModalScreen[str]):
    """Modal screen to input import file prefix"""

    CSS = """
    ImportPrefixScreen { align: center middle; }
    #import-dialog { width: 60; height: 10; border: thick $background 80%; background: $surface; padding: 1 2; }
    #import-prefix { margin: 1 0; }
    .import-help { color: $text-muted; }
    #import-buttons { height: 3; align: center middle; }
    #import-buttons Button { margin: 0 1; }
    """

    BINDINGS = [("escape", "cancel", "取消")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("📥 匯入設定 — 請輸入檔案名稱前綴:"),
            Label("  程式會讀取 {前綴}_filters.json + {前綴}_sensitive_columns.json", classes="import-help"),
            Input(placeholder="例如: Default", id="import-prefix"),
            Horizontal(
                Button("取消 [Esc]", id="cancel"),
                Button("匯入 [Enter]", variant="primary", id="do-import"),
                id="import-buttons"
            ),
            id="import-dialog"
        )

    def on_mount(self) -> None:
        self.query_one("#import-prefix", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prefix = event.value.strip()
        if prefix:
            self.dismiss(prefix)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "do-import":
            prefix = self.query_one("#import-prefix", Input).value.strip()
            if prefix:
                self.dismiss(prefix)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportProfileScreen(ModalScreen[str]):
    """Modal screen to input deploy profile export path"""

    CSS = """
    ExportProfileScreen { align: center middle; }
    #export-dialog { width: 70; height: 11; border: thick $background 80%; background: $surface; padding: 1 2; }
    #export-path { margin: 1 0; }
    .export-help { color: $text-muted; }
    #export-buttons { height: 3; align: center middle; margin-top: 1; }
    #export-buttons Button { margin: 0 1; }
    """

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, default_path: str) -> None:
        super().__init__()
        self.default_path = default_path

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("📤 匯出設定檔 — 請確認儲存路徑:"),
            Label("  此設定檔可用於 CLI 無人值守部署 (--deploy-profile)", classes="export-help"),
            Input(value=self.default_path, placeholder="輸入檔名...", id="export-path"),
            Horizontal(
                Button("取消 [Esc]", id="cancel"),
                Button("確認匯出 [Enter]", variant="primary", id="do-export"),
                id="export-buttons"
            ),
            id="export-dialog"
        )

    def on_mount(self) -> None:
        self.query_one("#export-path", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        if path:
            self.dismiss(path)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "do-export":
            path = self.query_one("#export-path", Input).value.strip()
            if path:
                self.dismiss(path)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConnectionScreen(ModalScreen):
    """Modal screen for editing per-project connection settings (v1.2.2)"""

    CSS = """
    ConnectionScreen { align: center middle; }
    #conn-dialog {
        width: 88; height: 40; border: thick $primary;
        background: $surface; padding: 1 2; overflow-y: auto;
    }
    .conn-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .section-header { text-style: bold reverse; background: $primary-darken-2; padding: 0 1; margin-top: 1; }
    .field-row { height: 3; margin: 0; }
    .field-label { width: 14; content-align: right middle; padding-right: 1; }
    .field-input { width: 1fr; }
    .eye-btn { width: 3; min-width: 3; margin-left: 1; background: $surface-darken-1; }
    .status-bar { height: 1; margin: 0 0 1 0; padding: 0 1; color: $text-muted; }
    .test-row { height: 3; align: right middle; margin: 0 0 1 0; }
    .btn-test { min-width: 20; background: $secondary; }
    #conn-actions { height: 4; align: center middle; margin-top: 1; border-top: solid $primary; }
    #conn-actions Button { margin: 0 1; min-width: 20; }
    #btn-save-conn { background: $success; }
    #btn-demo-conn { background: $warning; color: $background; }
    #btn-cancel-conn { background: $surface-darken-2; }
    """

    BINDINGS = [("escape", "cancel_conn", "取消"), ("enter", "save_conn", "儲存")]

    def __init__(self, project_id: int) -> None:
        super().__init__()
        self.project_id = project_id
        self.project = config_mgr.get_project_by_id(project_id)
        self._cfg = config_mgr.get_connection_config(project_id)
        self._src_pwd_masked = True
        self._tgt_pwd_masked = True
        self._src_testing = False
        self._tgt_testing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="conn-dialog"):
            yield Label(f"🔌 連線設定: {self.project.name}", classes="conn-title")
            yield Label(" 📤 來源資料庫 (Source) ", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Server:", classes="field-label")
                yield Input(value=self._cfg["src_server"], placeholder="IP / Hostname",
                            id="src-server", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Database:", classes="field-label")
                yield Input(value=self._cfg["src_database"], placeholder="資料庫名稱",
                            id="src-database", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("UID:", classes="field-label")
                yield Input(value=self._cfg["src_uid"], placeholder="使用者帳號",
                            id="src-uid", classes="field-input")
            with Horizontal(classes="field-row", id="src-pwd-row"):
                yield Label("Password:", classes="field-label")
                yield Input(value=self._cfg["src_pwd"], placeholder="密碼",
                            password=True, id="src-pwd", classes="field-input")
                yield Button("👁", id="btn-eye-src", classes="eye-btn")
            yield Label("", id="src-status", classes="status-bar")
            with Horizontal(classes="test-row"):
                yield Button("[測試來源連線]", id="btn-test-src", classes="btn-test")
            yield Label(" 📥 目標資料庫 (Target) ", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Server:", classes="field-label")
                yield Input(value=self._cfg["tgt_server"], placeholder="IP / Hostname",
                            id="tgt-server", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Database:", classes="field-label")
                yield Input(value=self._cfg["tgt_database"], placeholder="資料庫名稱",
                            id="tgt-database", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("UID:", classes="field-label")
                yield Input(value=self._cfg["tgt_uid"], placeholder="使用者帳號",
                            id="tgt-uid", classes="field-input")
            with Horizontal(classes="field-row", id="tgt-pwd-row"):
                yield Label("Password:", classes="field-label")
                yield Input(value=self._cfg["tgt_pwd"], placeholder="密碼",
                            password=True, id="tgt-pwd", classes="field-input")
                yield Button("👁", id="btn-eye-tgt", classes="eye-btn")
            yield Label("", id="tgt-status", classes="status-bar")
            with Horizontal(classes="test-row"):
                yield Button("[測試目標連線]", id="btn-test-tgt", classes="btn-test")
            with Horizontal(id="conn-actions"):
                yield Button("💾 儲存並連線 [Enter]", id="btn-save-conn",  variant="success")
                yield Button("🎭 Demo 模式",          id="btn-demo-conn",  variant="warning")
                yield Button("✖ 取消 [Esc]",          id="btn-cancel-conn", variant="default")

    def on_mount(self) -> None:
        cfg = self._cfg
        if not any([cfg["src_server"], cfg["src_uid"], cfg["tgt_server"], cfg["tgt_uid"]]):
            self.notify("💡 請填入連線資訊或選擇 Demo 模式", severity="information", timeout=6)

    def _toggle_password(self, side: str) -> None:
        field_id = f"{side}-pwd"
        is_masked_attr = f"_{side}_pwd_masked"
        current_input = self.query_one(f"#{field_id}", Input)
        new_masked = not getattr(self, is_masked_attr)
        setattr(self, is_masked_attr, new_masked)
        current_input.password = new_masked
        current_input.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-eye-src":
            self._toggle_password("src")
        elif bid == "btn-eye-tgt":
            self._toggle_password("tgt")
        elif bid == "btn-test-src":
            if not self._src_testing:
                self._src_testing = True
                event.button.disabled = True
                self.query_one("#src-status", Label).update("⏳ 測試中...")
                self.run_worker(self._test_connection("src"), exclusive=False)
        elif bid == "btn-test-tgt":
            if not self._tgt_testing:
                self._tgt_testing = True
                event.button.disabled = True
                self.query_one("#tgt-status", Label).update("⏳ 測試中...")
                self.run_worker(self._test_connection("tgt"), exclusive=False)
        elif bid == "btn-save-conn":
            self._do_save()
        elif bid == "btn-demo-conn":
            self._do_demo()
        elif bid == "btn-cancel-conn":
            self.dismiss(None)

    async def _test_connection(self, side: str) -> None:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        server   = self.query_one(f"#{side}-server",   Input).value.strip()
        database = self.query_one(f"#{side}-database",  Input).value.strip()
        uid      = self.query_one(f"#{side}-uid",       Input).value.strip()
        pwd      = self.query_one(f"#{side}-pwd",       Input).value
        status_label = self.query_one(f"#{side}-status", Label)
        btn_id = f"btn-test-{side}"

        def do_connect():
            import pymssql
            conn = pymssql.connect(
                server=server, database=database, user=uid,
                password=pwd, login_timeout=10,
            )
            conn.close()

        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as pool:
                await asyncio.wait_for(loop.run_in_executor(pool, do_connect), timeout=10.0)
            status_label.update("✅ 連線成功")
        except asyncio.TimeoutError:
            status_label.update("❌ 連線逾時 (10s)")
        except Exception as exc:
            status_label.update(f"❌ {str(exc)[:80]}")
        finally:
            try:
                self.query_one(f"#{btn_id}", Button).disabled = False
            except Exception:
                pass
            if side == "src":
                self._src_testing = False
            else:
                self._tgt_testing = False

    def _collect_fields(self) -> dict:
        return {
            "src_server":   self.query_one("#src-server",   Input).value.strip(),
            "src_database": self.query_one("#src-database",  Input).value.strip(),
            "src_uid":      self.query_one("#src-uid",       Input).value.strip(),
            "src_pwd":      self.query_one("#src-pwd",       Input).value,
            "tgt_server":   self.query_one("#tgt-server",   Input).value.strip(),
            "tgt_database": self.query_one("#tgt-database",  Input).value.strip(),
            "tgt_uid":      self.query_one("#tgt-uid",       Input).value.strip(),
            "tgt_pwd":      self.query_one("#tgt-pwd",       Input).value,
            "demo_mode":    False,
        }

    def _do_save(self) -> None:
        fields = self._collect_fields()
        if not fields["src_server"] or not fields["src_uid"]:
            self.notify("❌ 來源 Server 與 UID 為必填", severity="error")
            return
        if not fields["tgt_server"] or not fields["tgt_uid"]:
            self.notify("❌ 目標 Server 與 UID 為必填", severity="error")
            return
        config_mgr.save_connection_config(self.project_id, fields)
        self.dismiss("SAVED")

    def _do_demo(self) -> None:
        config_mgr.save_connection_config(self.project_id, {"demo_mode": True})
        self.dismiss("DEMO")

    def action_save_conn(self) -> None:
        self._do_save()

    def action_cancel_conn(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Project Selector App
# ---------------------------------------------------------------------------

class ProjectSelector(App):
    """App to select or manage projects"""
    CSS = """
    Screen { align: center middle; }
    #main-container { width: 64; height: 26; border: thick $primary; background: $surface; padding: 1 2; }
    .title { width: 100%; content-align: center middle; text-style: bold reverse; margin-bottom: 1; }
    #proj-list { height: 9; border: solid $secondary; margin: 1 0; overflow-y: auto; }
    #buttons { height: 4; align: center middle; margin-top: 1; }
    #buttons2 { height: 4; align: center middle; }
    Button { margin: 0 1; min-width: 12; }
    """

    BINDINGS = [
        ("n", "new_project",        "(N)ew"),
        ("c", "copy_project",        "(C)opy"),
        ("o", "open_project",        "(O)pen"),
        ("d", "drop_project",        "(D)rop"),
        ("i", "import_config",       "(I)mport"),
        ("e", "export_config",       "(E)xport"),
        ("l", "connection_settings", "(L)連線"),
        ("x", "exit_app",            "e(X)it"),
        ("question_mark", "show_info", "(?)"),
    ]

    def on_mount(self):
        self.refresh_list()
        list_view = self.query_one("#proj-list", ListView)
        list_view.focus()
        if self.projects:
            list_view.index = 0

    def refresh_list(self):
        self.projects = config_mgr.get_all_projects()
        list_view = self.query_one("#proj-list", ListView)
        prev_index = list_view.index
        list_view.clear()
        for p in self.projects:
            list_view.append(ListItem(Label(f"📁 {p.name}")))
        if self.projects:
            if prev_index is not None and prev_index < len(self.projects):
                list_view.index = prev_index
            else:
                list_view.index = 0

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(" 專案選擇 (Project Selector) ", classes="title"),
            ListView(id="proj-list"),
            Horizontal(
                Button("新建\n(N)ew", variant="success", id="new"),
                Button("複製\n(C)opy", variant="warning", id="clone"),
                Button("開啟\n(O)pen", variant="primary", id="open"),
                Button("刪除\n(D)rop", variant="error", id="delete"),
                id="buttons"
            ),
            Horizontal(
                Button("匯入\n(I)mport", variant="primary", id="import"),
                Button("匯出\n(E)xport", variant="primary", id="export"),
                Button("連線\n(L)", variant="primary", id="conn"),
                Button("離開\ne(X)it", variant="error", id="exit"),
                Button("說明\n(?)", variant="default", id="info"),
                id="buttons2"
            ),
            id="main-container"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._open_selected()

    def action_new_project(self) -> None:
        self._do_new()

    def action_copy_project(self) -> None:
        self._do_clone()

    def action_open_project(self) -> None:
        self._open_selected()

    def action_drop_project(self) -> None:
        self._do_delete()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "new":
            self._do_new()
        elif event.button.id == "open":
            self._open_selected()
        elif event.button.id == "clone":
            self._do_clone()
        elif event.button.id == "delete":
            self._do_delete()
        elif event.button.id == "import":
            self._do_import()
        elif event.button.id == "export":
            self._do_export()
        elif event.button.id == "conn":
            self.action_connection_settings()
        elif event.button.id == "exit":
            self.exit(None)
        elif event.button.id == "info":
            self.push_screen(InfoScreen())

    def _do_new(self):
        def on_new(name):
            if name:
                try:
                    config_mgr.create_project(name)
                    self.refresh_list()
                except Exception as e:
                    self.notify(f"Error: {e}", severity="error")
        self.push_screen(NewProjectScreen(), on_new)

    def _do_clone(self):
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is not None:
            source = self.projects[list_view.index]
            def on_clone_name(name):
                if name:
                    try:
                        config_mgr.clone_project(source.id, name)
                        self.refresh_list()
                        self.notify(f"✅ 已複製 [{source.name}] → [{name}]")
                    except Exception as e:
                        self.notify(f"❌ 複製失敗: {e}", severity="error")
            self.push_screen(NewProjectScreen(), on_clone_name)
        else:
            self.notify("請先選擇要複製的專案", severity="warning")

    def _do_delete(self):
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is not None:
            p = self.projects[list_view.index]
            if p.name == "Default":
                self.notify("無法刪除預設專案", severity="error")
                return
            config_mgr.delete_project(p.id)
            self.refresh_list()

    def _open_selected(self):
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is not None:
            project = self.projects[list_view.index]
            self.exit(project.id)
        else:
            self.notify("請選擇一個專案")

    def action_import_config(self) -> None:
        self._do_import()

    def action_export_config(self) -> None:
        self._do_export()

    def action_exit_app(self) -> None:
        self.exit(None)

    def action_show_info(self) -> None:
        self.push_screen(InfoScreen())

    def action_connection_settings(self) -> None:
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is None:
            self.notify("請先選擇專案", severity="warning")
            return
        project = self.projects[list_view.index]
        def _on_conn_result(result):
            if result == "SAVED":
                self.notify("✅ 連線設定已儲存")
            elif result == "DEMO":
                self.notify("🎭 Demo 模式已設定")
        self.push_screen(ConnectionScreen(project.id), _on_conn_result)

    def _do_import(self):
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is None:
            self.notify("請先選擇要匯入的專案", severity="warning")
            return
        project = self.projects[list_view.index]
        def on_prefix(prefix):
            if prefix:
                try:
                    config_mgr.import_from_json(project.id, prefix)
                    self.notify(f"✅ 已匯入 [{prefix}] 設定到專案 [{project.name}]")
                except FileNotFoundError as e:
                    self.notify(f"❌ {e}", severity="error")
                except Exception as e:
                    self.notify(f"❌ 匯入失敗: {e}", severity="error")
        self.push_screen(ImportPrefixScreen(), on_prefix)

    def _do_export(self):
        list_view = self.query_one("#proj-list", ListView)
        if list_view.index is None:
            self.notify("請先選擇要匯出的專案", severity="warning")
            return
        project = self.projects[list_view.index]
        try:
            f_path, s_path = config_mgr.export_to_json(project.id)
            self.notify(f"✅ 已匯出:\n  {f_path}\n  {s_path}")
        except Exception as e:
            self.notify(f"❌ 匯出失敗: {e}", severity="error")


# ---------------------------------------------------------------------------
# Table Selector App
# ---------------------------------------------------------------------------

class TableItem(ListItem):
    """Custom ListItem with checkbox-like behavior"""
    def __init__(self, name: str) -> None:
        super().__init__()
        self.table_name = name
        self.checked = False
        self.label = Label(f"[ ] {name}")

    def compose(self) -> ComposeResult:
        yield self.label

    def on_click(self) -> None:
        self.toggle()

    def toggle(self) -> None:
        self.checked = not self.checked
        mark = "[x]" if self.checked else "[ ]"
        self.label.update(f"{mark} {self.table_name}")


class ConfirmScreen(ModalScreen[bool]):
    """Modal screen for double confirmation"""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog {
        grid-size: 2; grid-gutter: 1 2; grid-rows: 1fr 3;
        padding: 0 1; width: 60; height: 11;
        border: thick $background 80%; background: $surface;
    }
    #question { column-span: 2; height: 1fr; content-align: center middle; }
    Label#help { column-span: 2; content-align: center middle; }
    """

    BINDINGS = [("y", "submit(True)", "Yes"), ("n", "submit(False)", "No")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.message, id="question"),
            Label("確認y 取消n", id="help"),
            id="dialog"
        )

    def action_submit(self, result: bool) -> None:
        self.dismiss(result)


class FilterEditorScreen(ModalScreen[Optional[str]]):
    """Modal screen for editing table filter condition"""

    CSS = """
    FilterEditorScreen { align: center middle; }
    #filter-editor-dialog { width: 80; height: 20; border: thick $background 80%; background: $surface; padding: 1 2; }
    #filter-editor-dialog Label { margin-bottom: 1; }
    #filter-input { height: 8; margin-bottom: 1; }
    #filter-buttons { height: 3; align: center middle; }
    #filter-buttons Button { margin: 0 2; }
    """

    BINDINGS = [("escape", "cancel", "取消"), ("ctrl+enter", "save", "儲存")]

    def __init__(self, table_name: str, current_filter: str) -> None:
        super().__init__()
        self.table_name = table_name
        self.current_filter = current_filter or ""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"📝 編輯 [{self.table_name}] 的篩選條件 (WHERE clause):"),
            TextArea(self.current_filter, id="filter-input"),
            Label("提示: 直接輸入 SQL WHERE 條件，例如: data_year > '114' AND mm > '3'", classes="help"),
            Horizontal(
                Button("取消 [Esc]", variant="default", id="cancel"),
                Button("套用 [Ctrl+Enter]", variant="primary", id="save"),
                id="filter-buttons"
            ),
            id="filter-editor-dialog"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            self.dismiss(self.query_one("#filter-input", TextArea).text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self.query_one("#filter-input", TextArea).text.strip())


class PIIEditorScreen(ModalScreen[Optional[dict]]):
    """Modal screen for editing PII/sensitive column rules"""

    CSS = """
    PIIEditorScreen { align: center middle; }
    #pii-editor-dialog { width: 90; height: 25; border: thick $background 80%; background: $surface; padding: 1 2; }
    #pii-editor-dialog Label { margin-bottom: 1; }
    #pii-input { height: 12; margin-bottom: 1; }
    .help { color: $text-muted; }
    #pii-buttons { height: 3; align: center middle; }
    #pii-buttons Button { margin: 0 2; }
    """

    BINDINGS = [("escape", "cancel", "取消"), ("ctrl+enter", "save", "儲存")]

    def __init__(self, table_name: str, current_rules: dict) -> None:
        super().__init__()
        self.table_name = table_name
        self.rules_json = json.dumps(current_rules, ensure_ascii=False, indent=2) if current_rules else "{}"

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"🔒 編輯 [{self.table_name}] 的 PII 去敏化規則 (JSON 格式):"),
            TextArea(self.rules_json, id="pii-input"),
            Label('格式: {"欄位名": ["函數名", "seed欄位或null"], ...}', classes="help"),
            Label("可用函數: obfuscate_name, anonymize_id, obfuscate_address, obfuscate_phone, clear_content", classes="help"),
            Horizontal(
                Button("取消 [Esc]", variant="default", id="cancel"),
                Button("套用 [Ctrl+Enter]", variant="primary", id="save"),
                id="pii-buttons"
            ),
            id="pii-editor-dialog"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            self._try_save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self._try_save()

    def _try_save(self) -> None:
        text = self.query_one("#pii-input", TextArea).text.strip()
        if not text or text == "{}":
            self.dismiss({})
            return
        try:
            rules = json.loads(text)
            converted = {}
            for col, val in rules.items():
                if isinstance(val, list) and len(val) == 2:
                    converted[col] = (val[0], val[1])
                else:
                    converted[col] = val
            self.dismiss(converted)
        except json.JSONDecodeError as e:
            self.notify(f"❌ JSON 格式錯誤: {e}", severity="error")


class TableSelector(App):
    """Textual App for selecting tables with 3-column layout"""

    CSS = """
    #main-container { height: 1fr; }
    #columns-container { height: 1fr; }
    .column { border: solid $primary; padding: 0 1; }
    #tables-column { width: 2fr; }
    #filters-column { width: 1.5fr; }
    #pii-column { width: 1.5fr; }
    .column-header { background: $accent; text-style: bold; padding: 0 1; }
    .column-header:hover { background: $accent-lighten-1; text-style: bold underline; }
    #table-list { height: 1fr; border: none; }
    ListItem { padding: 0 1; }
    ListItem:hover { background: $primary-background; }
    .panel-upper { height: 1fr; border-bottom: solid $primary; padding: 0 1; overflow-y: auto; }
    .panel-lower { height: 1fr; padding: 0; }
    #tab-bar { height: 1; padding: 0 1; background: $surface-lighten-1; }
    .tab-label { padding: 0 2; content-align: center middle; }
    .tab-label-active { text-style: bold; color: $accent; }
    #ddl-column { width: 3fr; display: none; }
    #ddl-preview { height: 1fr; border: none; }
    #trigger-warning { height: 2; color: $warning; text-style: bold; border: solid $warning; padding: 0 1; display: none; }
    .panel-lower-header { background: $accent-darken-2; text-style: bold; padding: 0 1; height: auto; }
    .metadata-scroll { height: 1fr; overflow-y: scroll; padding: 0 1; }
    .metadata-scroll:focus { border: solid $accent; }
    .info-bar { height: 1; padding: 0 1; }
    #project-badge { background: $primary; color: $warning; text-style: bold; padding: 0 1; min-width: 20; height: 1; border: none; }
    #project-badge:hover { background: $primary-lighten-1; text-style: bold underline; }
    #info-hints { padding: 0 1; }
    #info-hints:hover { color: $warning; }
    .has-rule { color: $success; }
    .no-rule { color: $text-muted; }
    .pk-list, .column-list { color: $text; }
    """

    BINDINGS = [
        Binding("a", "select_all", "全選"),
        Binding("space", "toggle_current", "選取"),
        Binding("f", "edit_filter", "篩選"),
        Binding("p", "edit_pii", "PII"),
        Binding("o", "project_settings", "設定"),
        Binding("s", "save_configs", "存檔"),
        Binding("g", "initiate_confirm", "開始"),
        Binding("1", "switch_tab_table", "Tables", show=False),
        Binding("2", "switch_tab_view", "Views", show=False),
        Binding("3", "switch_tab_sp", "SPs", show=False),
        Binding("4", "switch_tab_function", "Functions", show=False),
        Binding("5", "switch_tab_trigger", "Triggers", show=False),
        Binding("ctrl+o", "back_to_project", "切換專案", show=False),
        Binding("tab", "focus_next", "下個區域", show=False),
        Binding("shift+tab", "focus_previous", "上個區域", show=False),
        Binding("x", "export_profile", "匯出設定檔"),
        Binding("q", "quit", "離開"),
    ]

    def __init__(self, project_id: int, objects_dict: Dict[str, List[str]], inspector=None):
        super().__init__()
        self.project_id = project_id
        self.project = config_mgr.get_project_by_id(project_id)
        self.objects_dict = objects_dict
        self.all_selected = False
        self.current_object: Optional[str] = None
        self.configs_modified = False
        self.inspector = inspector
        self.table_columns_cache: Dict[str, List[str]] = {}
        self.table_pk_cache: Dict[str, List[str]] = {}
        self.current_tab: str = "TABLE"
        self._load_config_for_tab()

    def _load_config_for_tab(self):
        selected_set, filters, pii_rules = config_mgr.get_project_config_by_type(self.project_id, self.current_tab)
        self.initial_checked = selected_set
        if self.current_tab == "TABLE":
            _, self.filters, self.pii_rules, _ = config_mgr.get_project_config(self.project_id)

    def on_mount(self) -> None:
        self._refresh_tab_bar()
        self._reload_object_list()
        self.query_one("#table-list", ListView).focus()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tab-bar"):
            yield Label("", id="tab-table", classes="tab-label")
            yield Label("", id="tab-view", classes="tab-label")
            yield Label("", id="tab-sp", classes="tab-label")
            yield Label("", id="tab-function", classes="tab-label")
            yield Label("", id="tab-trigger", classes="tab-label")
        with Horizontal(classes="info-bar"):
            yield Button(f" 專案^O: {self.project.name} ", id="project-badge")
            yield Label(" Space_選取 F_篩選 P_PII O_設定 S_存檔 G_開始", id="info-hints")
        with Horizontal(id="columns-container"):
            with Vertical(id="tables-column", classes="column"):
                yield Label("📋 OBJECTS", id="list-header", classes="column-header")
                yield ListView(id="table-list")
            with Vertical(id="filters-column", classes="column"):
                yield Label("🔍 DATA_FILTERS >F", classes="column-header")
                with Vertical(classes="panel-upper"):
                    yield Static("選擇資料表以查看篩選條件", id="filter-display", classes="no-rule")
                with Vertical(classes="panel-lower"):
                    yield Label("🔑 Primary Keys", classes="panel-lower-header")
                    with ScrollableContainer(id="pk-scroll", classes="metadata-scroll"):
                        yield Static("", id="pk-list", classes="pk-list")
            with Vertical(id="pii-column", classes="column"):
                yield Label("🔒 PII COLUMNS >P", classes="column-header")
                with Vertical(classes="panel-upper"):
                    yield Static("選擇資料表以查看 PII 規則", id="pii-display", classes="no-rule")
                with Vertical(classes="panel-lower"):
                    yield Label("📋 All Columns", classes="panel-lower-header")
                    with ScrollableContainer(id="col-scroll", classes="metadata-scroll"):
                        yield Static("", id="column-list", classes="column-list")
            with Vertical(id="ddl-column", classes="column"):
                yield Label("📄 DDL PREVIEW / 🔗 DEPENDENCIES", classes="column-header")
                yield Label("⚠️  警告：複製 Trigger 可能干擾目標 DB 的資料寫入與複製流程，請謹慎評估。", id="trigger-warning")
                with Vertical(classes="panel-upper"):
                    yield TextArea("", read_only=True, id="ddl-preview")
                with Vertical(classes="panel-lower"):
                    yield Label("🔗 Dependencies", classes="panel-lower-header")
                    with ScrollableContainer(id="dep-scroll", classes="metadata-scroll"):
                        yield Static("", id="dep-list")
        yield Footer()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, TableItem):
            self.current_object = event.item.table_name
            self._update_side_panels()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-badge":
            self.action_back_to_project()

    def _load_table_metadata(self, table_name: str) -> None:
        if table_name in self.table_columns_cache:
            return
        if self.inspector:
            try:
                columns = self.inspector.get_columns(table_name)
                self.table_columns_cache[table_name] = [c['name'] for c in columns]
                pk_constraint = self.inspector.get_pk_constraint(table_name)
                self.table_pk_cache[table_name] = pk_constraint.get('constrained_columns', [])
            except Exception as e:
                self.notify(f"⚠️ 無法載入 {table_name} 元數據: {e}", severity="warning")
                self.table_columns_cache[table_name] = []
                self.table_pk_cache[table_name] = []
        else:
            self.table_columns_cache[table_name] = [f"column_{i}" for i in range(1, 16)]
            self.table_pk_cache[table_name] = ["id", "seq_no"]

    def _update_side_panels(self) -> None:
        if not self.current_object:
            return
        current_obj = self.current_object
        if self.current_tab == "TABLE":
            self._load_table_metadata(current_obj)
            filter_display = self.query_one("#filter-display", Static)
            filter_rule = self.filters.get(current_obj)
            if filter_rule:
                filter_display.update(f"✅ WHERE:\n{filter_rule}")
                filter_display.remove_class("no-rule")
                filter_display.add_class("has-rule")
            else:
                filter_display.update("無篩選條件\n\n按 F 新增")
                filter_display.remove_class("has-rule")
                filter_display.add_class("no-rule")
            pk_list = self.query_one("#pk-list", Static)
            pks = self.table_pk_cache.get(current_obj, [])
            pk_list.update("\n".join([f"  • {pk}" for pk in pks]) if pks else "  (無主鍵)")
            pii_display = self.query_one("#pii-display", Static)
            pii_rules = self.pii_rules.get(current_obj)
            if pii_rules:
                lines = ["✅ 去敏化規則:"]
                for col, (func_name, seed_col) in pii_rules.items():
                    seed_str = f" (seed: {seed_col})" if seed_col else ""
                    lines.append(f"  • {col}: {func_name}{seed_str}")
                pii_display.update("\n".join(lines))
                pii_display.remove_class("no-rule")
                pii_display.add_class("has-rule")
            else:
                pii_display.update("無去敏化規則\n\n按 P 新增")
                pii_display.remove_class("has-rule")
                pii_display.add_class("no-rule")
            column_list = self.query_one("#column-list", Static)
            columns = self.table_columns_cache.get(current_obj, [])
            column_list.update(
                "\n".join([f"  • {col}" for col in columns]) if columns else "  (無欄位資訊)"
            )
        else:
            if self.inspector:
                engine = self.inspector.bind
                ddl  = fetch_ddl(engine, current_obj, self.current_tab)
                deps = fetch_dependencies(engine, current_obj)
            else:
                ddl  = f"-- Mock DDL for {current_obj}\nCREATE {self.current_tab} {current_obj} AS ...\n"
                deps = [{"name": "MOCK_TABLE", "type": "TABLE"}]
            self.query_one("#ddl-preview", TextArea).load_text(ddl or "(無法取得定義)")
            dep_text = "\n".join([f"  • {d['name']} ({d['type']})" for d in deps]) or "  (無相依物件)"
            self.query_one("#dep-list", Static).update(dep_text)

    def action_switch_tab_table(self): self._switch_tab("TABLE")
    def action_switch_tab_view(self): self._switch_tab("VIEW")
    def action_switch_tab_sp(self): self._switch_tab("SP")
    def action_switch_tab_function(self): self._switch_tab("FUNCTION")
    def action_switch_tab_trigger(self): self._switch_tab("TRIGGER")

    def _switch_tab(self, tab_name: str) -> None:
        self.action_save_configs()
        self.current_tab = tab_name
        is_table = (tab_name == "TABLE")
        self.query_one("#filters-column").display = is_table
        self.query_one("#pii-column").display = is_table
        self.query_one("#ddl-column").display = not is_table
        self.query_one("#trigger-warning", Label).display = (tab_name == "TRIGGER")
        self.query_one("#list-header", Label).update(f"📋 {tab_name}S")
        self._load_config_for_tab()
        self._refresh_tab_bar()
        self._reload_object_list()
        self._clear_side_panels()

    def _refresh_tab_bar(self) -> None:
        tabs = {
            "TABLE":   ("tab-table",   "[1] TABLES"),
            "VIEW":    ("tab-view",    "[2] VIEWS"),
            "SP":      ("tab-sp",      "[3] SPs"),
            "FUNCTION":("tab-function","[4] FUNCTIONS"),
            "TRIGGER": ("tab-trigger", "[5] TRIGGERS ⚠️"),
        }
        for t_name, (lbl_id, base_text) in tabs.items():
            lbl = self.query_one(f"#{lbl_id}", Label)
            if t_name == self.current_tab:
                parts = base_text.split(" ", 1)
                lbl.update(f"{parts[0]} >> {parts[1]} <<")
                lbl.add_class("tab-label-active")
            else:
                lbl.update(base_text)
                lbl.remove_class("tab-label-active")

    def _reload_object_list(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        prev_index = list_view.index
        list_view.clear()
        objects = self.objects_dict.get(self.current_tab, [])
        items = []
        for name in objects:
            item = TableItem(name)
            if name in self.initial_checked:
                item.checked = True
                item.label.update(f"[x] {name}")
            items.append(item)
        list_view.extend(items)
        if objects:
            if prev_index is not None and prev_index < len(objects):
                list_view.index = prev_index
            else:
                list_view.index = 0
            self.current_object = objects[list_view.index]
        else:
            self.current_object = None
        self._update_side_panels()

    def _clear_side_panels(self) -> None:
        if self.current_tab != "TABLE":
            self.query_one("#ddl-preview", TextArea).load_text("")
            self.query_one("#dep-list", Static).update("")

    def action_toggle_current(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        if list_view.highlighted_child:
            list_view.highlighted_child.toggle()

    def action_select_all(self) -> None:
        self.all_selected = not self.all_selected
        list_view = self.query_one("#table-list", ListView)
        for item in list_view.children:
            if isinstance(item, TableItem) and item.checked != self.all_selected:
                item.toggle()
        self.notify(f"{'已全選' if self.all_selected else '已取消全選'}")

    def action_edit_filter(self) -> None:
        if not self.current_object:
            self.notify("請先選擇資料表", severity="warning")
            return
        current_filter = self.filters.get(self.current_object, "")
        def on_filter_result(result: Optional[str]) -> None:
            if result is not None:
                if result:
                    self.filters[self.current_object] = result
                    self.notify(f"✅ 已更新 {self.current_object} 的篩選條件")
                else:
                    self.filters.pop(self.current_object, None)
                    self.notify(f"🗑️ 已移除 {self.current_object} 的篩選條件")
                self.configs_modified = True
                self._update_side_panels()
        self.push_screen(FilterEditorScreen(self.current_object, current_filter), on_filter_result)

    def action_edit_pii(self) -> None:
        if not self.current_object:
            self.notify("請先選擇資料表", severity="warning")
            return
        current_rules = self.pii_rules.get(self.current_object, {})
        def on_pii_result(result: Optional[dict]) -> None:
            if result is not None:
                if result:
                    self.pii_rules[self.current_object] = result
                    self.notify(f"✅ 已更新 {self.current_object} 的 PII 規則")
                else:
                    self.pii_rules.pop(self.current_object, None)
                    self.notify(f"🗑️ 已移除 {self.current_object} 的 PII 規則")
                self.configs_modified = True
                self._update_side_panels()
        self.push_screen(PIIEditorScreen(self.current_object, current_rules), on_pii_result)

    def action_back_to_project(self) -> None:
        self.exit("__BACK_TO_PROJECT__")

    def action_project_settings(self) -> None:
        def on_settings_changed(changed: bool):
            if changed:
                self.project = config_mgr.get_project_by_id(self.project_id)
        self.push_screen(ProjectSettingsScreen(self.project_id), on_settings_changed)

    def action_save_configs(self) -> bool:
        try:
            list_view = self.query_one("#table-list", ListView)
            selected = [
                item.table_name for item in list_view.children
                if isinstance(item, TableItem) and item.checked
            ]
            config_mgr.save_project_state_by_type(self.project_id, self.current_tab, selected)
            if self.current_tab == "TABLE":
                config_mgr.save_project_state(self.project_id, selected, self.filters, self.pii_rules)
            self.configs_modified = False
            self.notify(f"✅ {self.current_tab} 設定已儲存 (DB)")
            return True
        except Exception as e:
            self.notify(f"❌ 儲存失敗: {e}", severity="error")
            return False

    def action_export_profile(self) -> None:
        try:
            has_selection = False
            for t in ["TABLE", "VIEW", "SP", "FUNCTION", "TRIGGER"]:
                sel, _, _ = config_mgr.get_project_config_by_type(self.project_id, t)
                if sel:
                    has_selection = True
                    break
            list_view = self.query_one("#table-list", ListView)
            current_selected = [
                item.table_name for item in list_view.children
                if isinstance(item, TableItem) and item.checked
            ]
            if current_selected:
                has_selection = True
            if not has_selection:
                self.notify("⚠️ 尚未選取任何物件，無法匯出", severity="warning")
                return
            db_selected, _, _ = config_mgr.get_project_config_by_type(self.project_id, self.current_tab)
            is_different = set(current_selected) != set(db_selected)
            if not self.action_save_configs():
                self.notify("❌ 儲存失敗，無法匯出。請確認 DB 狀態後重試。", severity="error")
                return
            if is_different:
                self.notify("💾 設定已更新並儲存")
            else:
                self.notify("✅ 設定無差異，已確認")
            default_path = f"{self.project.name.replace(' ', '_')}_deploy_profile.json"
            def on_export_confirm(output_path: Optional[str]):
                if output_path:
                    try:
                        abs_path = config_mgr.export_deploy_profile(self.project_id, output_path)
                        self.notify(f"✅ 已匯出：{abs_path}", timeout=6)
                        sel_triggers, _, _ = config_mgr.get_project_config_by_type(self.project_id, "TRIGGER")
                        if sel_triggers:
                            self.notify("⚠️ 注意：設定檔包含 Trigger，請確認對目標 DB 無干擾", severity="warning", timeout=8)
                    except Exception as ex:
                        self.notify(f"❌ 匯出失敗: {ex}", severity="error")
            self.push_screen(ExportProfileScreen(default_path), on_export_confirm)
        except Exception as e:
            self.notify(f"❌ 匯出流程錯誤: {e}", severity="error")

    def action_initiate_confirm(self) -> None:
        if not self.action_save_configs():
            self.notify("❌ 儲存失敗，無法繼續。請確認 DB 狀態後重試。", severity="error")
            return
        payload = {}
        for t in ["TABLE", "VIEW", "SP", "FUNCTION", "TRIGGER"]:
            sel, _, _ = config_mgr.get_project_config_by_type(self.project_id, t)
            payload[t.lower() + "s"] = list(sel)
        total_selected = sum(len(v) for v in payload.values())
        if total_selected == 0:
            self.notify("請至少選擇一個物件！", severity="error")
            return
        warning = "\n\n⚠️ 已選取 Trigger，複製時可能影響資料寫入，確定繼續？" if payload.get("triggers") else ""
        msg = (
            f"已選擇：\n"
            f"  資料表：{len(payload.get('tables', []))} 個\n"
            f"  檢視表：{len(payload.get('views', []))} 個\n"
            f"  預存程序：{len(payload.get('sps', []))} 個\n"
            f"  函數：{len(payload.get('functions', []))} 個\n"
            f"  觸發器：{len(payload.get('triggers', []))} 個{'  ⚠️' if payload.get('triggers') else ''}\n"
            f"{warning}"
        )
        def check_confirm(is_confirmed: bool) -> None:
            if is_confirmed:
                self.exit(payload)
        self.push_screen(ConfirmScreen(msg), check_confirm)
