import sys
import importlib
import time
from datetime import datetime
import pandas as pd
import os
import argparse
import json
from typing import List, Dict, Optional

import logging

# --- Logging Setup ---
# Generate Log Filename based on date
CURRENT_DATE_STR = datetime.now().strftime('%Y%m%d')
LOG_FILENAME = f"{CURRENT_DATE_STR}_Clone.log"
DATE_SALT = CURRENT_DATE_STR  # Use same date string for salt

# Configure Logging
logger = logging.getLogger("DB_Replicator")
logger.setLevel(logging.DEBUG)  # Capture all levels, handlers will filter

# File Handler (Detailed logs including DEBUG)
file_handler = logging.FileHandler(LOG_FILENAME, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Console Handler (Info level for user interaction)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s') # Keep console clean
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --- Configuration Section ---

# Helper to load sideload configs
def load_sideload_config(name: str, default_val: dict) -> dict:
    filename = f"{name.lower()}.json"
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                logger.info(f"Loading external config: {filename}")
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Error loading {filename}: {e}. Using compiled-in defaults.")
    return default_val

# 大型資料表篩選 - 支援多條件
# 格式: 'TABLE_NAME': "condition1 AND condition2"
DEFAULT_LARGE_TABLE_FILTERS = {
    'WORK_TIME_REC': "data_year > '114' AND mm > '3'",
    'SALARY_DETAIL': "data_year >= '113' AND data_year <= '114'",
    'SYSTEM_LOG': "log_year > '113' AND log_mm >= '06'",
    'ATTENDANCE_REC': "data_year = '114'",
}
LARGE_TABLE_FILTERS = load_sideload_config('LARGE_TABLE_FILTERS', DEFAULT_LARGE_TABLE_FILTERS)

# 需要去識別化的欄位對應
# 格式: 'TABLE_NAME': {'column': ('function_name', 'seed_column_or_None')}
DEFAULT_SENSITIVE_COLUMNS = {
    'EMP_DATA': {
        'emp_name': ('obfuscate_name', 'emp_no'),
        'emp_ename': ('clear_content', None),
        'license_id': ('anonymize_id', None),
        'address': ('obfuscate_address', 'emp_no'),
        'home_addr': ('obfuscate_address', 'emp_no'),
        'emer_member': ('obfuscate_spouse_name', 'emp_no'),
        'tel': ('obfuscate_phone', 'emp_no'),
        'mobile': ('obfuscate_phone', 'emp_no'),
        'emer_tel': ('obfuscate_phone', 'emp_no'),
        'emer_mobile': ('obfuscate_phone', 'emp_no'),
        'zap_address': ('obfuscate_address', 'emp_no'),
        'con_address': ('obfuscate_address', 'emp_no'),
    },
    'ADVANCE_BONUS_GRANT': {
        'emp_name': ('obfuscate_name', 'emp_no'),
    },
    'DEPENDENT_DATA': {
        'dep_name': ('obfuscate_name', 'emp_no'),
        'dep_id_no': ('anonymize_id', None),
    }
}
SENSITIVE_COLUMNS = load_sideload_config('SENSITIVE_COLUMNS', DEFAULT_SENSITIVE_COLUMNS)


REQUIRED_PACKAGES = {
    'sqlalchemy': 'SQLAlchemy',
    'pandas': 'pandas', 
    'pyodbc': 'pyodbc',
    'tqdm': 'tqdm',
    'textual': 'textual'
}

# --- Dependency Check ---

def check_dependencies():
    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        logger.error(f"缺少套件: {', '.join(missing)}")
        logger.error(f"請執行: pip install {' '.join(missing)}")
        sys.exit(1)

check_dependencies()

# Imports after check
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.types import NVARCHAR
from tqdm import tqdm
from data_anonymizer import (
    obfuscate_name, anonymize_id, obfuscate_address, 
    initialize_name_data, obfuscate_spouse_name, obfuscate_phone,
    clear_content
)

# --- Textual TUI App ---

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Label, Button, Static, TextArea
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.message import Message


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
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 0 1;
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: $surface;
    }
    #question {
        column-span: 2;
        height: 1fr;
        content-align: center middle;
    }
    Label#help {
        column-span: 2;
        content-align: center middle;
    }
    """

    BINDINGS = [
        ("y", "submit(True)", "Yes"),
        ("n", "submit(False)", "No"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.message, id="question"),
            Label("[Y] 確認  [N] 取消", id="help"),
            id="dialog"
        )

    def action_submit(self, result: bool) -> None:
        self.dismiss(result)


class FilterEditorScreen(ModalScreen[str | None]):
    """Modal screen for editing table filter condition"""
    
    CSS = """
    FilterEditorScreen {
        align: center middle;
    }
    #filter-editor-dialog {
        width: 80;
        height: 20;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #filter-editor-dialog Label {
        margin-bottom: 1;
    }
    #filter-input {
        height: 8;
        margin-bottom: 1;
    }
    #filter-buttons {
        height: 3;
        align: center middle;
    }
    #filter-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("ctrl+enter", "save", "儲存"),
    ]

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
            text_area = self.query_one("#filter-input", TextArea)
            self.dismiss(text_area.text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        text_area = self.query_one("#filter-input", TextArea)
        self.dismiss(text_area.text.strip())


class PIIEditorScreen(ModalScreen[dict | None]):
    """Modal screen for editing PII/sensitive column rules"""
    
    CSS = """
    PIIEditorScreen {
        align: center middle;
    }
    #pii-editor-dialog {
        width: 90;
        height: 25;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #pii-editor-dialog Label {
        margin-bottom: 1;
    }
    #pii-input {
        height: 12;
        margin-bottom: 1;
    }
    .help {
        color: $text-muted;
    }
    #pii-buttons {
        height: 3;
        align: center middle;
    }
    #pii-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("ctrl+enter", "save", "儲存"),
    ]

    def __init__(self, table_name: str, current_rules: dict) -> None:
        super().__init__()
        self.table_name = table_name
        # Convert rules to JSON for editing
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
        text_area = self.query_one("#pii-input", TextArea)
        text = text_area.text.strip()
        if not text or text == "{}":
            self.dismiss({})
            return
        try:
            rules = json.loads(text)
            # Convert list format to tuple format expected by the system
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
    #main-container {
        height: 1fr;
    }
    #columns-container {
        height: 1fr;
    }
    .column {
        border: solid $primary;
        padding: 0 1;
    }
    #tables-column {
        width: 2fr;
    }
    #filters-column {
        width: 1.5fr;
    }
    #pii-column {
        width: 1.5fr;
    }
    .column-header {
        background: $accent;
        text-style: bold;
        padding: 0 1;
    }
    #table-list {
        height: 1fr;
        border: none;
    }
    /* Split panel styles */
    .panel-upper {
        height: 1fr;
        border-bottom: solid $primary;
        padding: 0 1;
        overflow-y: auto;
    }
    .panel-lower {
        height: 1fr;
        padding: 0;
    }
    .panel-lower-header {
        background: $accent-darken-2;
        text-style: bold;
        padding: 0 1;
        height: auto;
    }
    .metadata-scroll {
        height: 1fr;
        overflow-y: scroll;
        padding: 0 1;
    }
    .metadata-scroll:focus {
        border: solid $accent;
    }
    .info {
        padding: 1;
    }
    .has-rule {
        color: $success;
    }
    .no-rule {
        color: $text-muted;
    }
    .pk-list, .column-list {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("a", "select_all", "全選/取消"),
        Binding("space", "toggle_current", "選取"),
        Binding("f", "edit_filter", "編輯篩選[F]"),
        Binding("p", "edit_pii", "編輯PII[P]"),
        Binding("s", "save_configs", "存檔[S]"),
        Binding("g", "initiate_confirm", "確認開始[G]"),
        Binding("tab", "focus_next", "下個區域", show=False),
        Binding("shift+tab", "focus_previous", "上個區域", show=False),
        ("q", "quit", "離開"),
    ]

    def __init__(self, table_names: List[str], inspector=None):
        super().__init__()
        self.table_names = table_names
        self.selected_tables = []
        self.all_selected = False
        self.current_table: Optional[str] = None
        self.configs_modified = False
        # Database inspector for metadata loading
        self.inspector = inspector
        # Lazy-loaded metadata caches
        self.table_columns_cache: Dict[str, List[str]] = {}
        self.table_pk_cache: Dict[str, List[str]] = {}

    def on_mount(self) -> None:
        self.query_one("#table-list", ListView).focus()
        # Initialize display with first table if available
        if self.table_names:
            self.current_table = self.table_names[0]
            self._update_side_panels()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(
            f"偵測到 {len(self.table_names)} 個資料表。Space_選取 F_編輯篩選 P_編輯PII S_存檔 G_開始 TAB_切換區域",
            classes="info"
        )
        
        with Horizontal(id="columns-container"):
            # Column 1: Tables
            with Vertical(id="tables-column", classes="column"):
                yield Label("📋 TABLES", classes="column-header")
                items = [TableItem(name) for name in self.table_names]
                yield ListView(*items, id="table-list")
            
            # Column 2: DATA_FILTERS - split upper/lower
            with Vertical(id="filters-column", classes="column"):
                yield Label("🔍 DATA_FILTERS >F", classes="column-header")
                # Upper: Current filter rules
                with Vertical(classes="panel-upper"):
                    yield Static("選擇資料表以查看篩選條件", id="filter-display", classes="no-rule")
                # Lower: Primary Keys
                with Vertical(classes="panel-lower"):
                    yield Label("🔑 Primary Keys", classes="panel-lower-header")
                    with ScrollableContainer(id="pk-scroll", classes="metadata-scroll"):
                        yield Static("", id="pk-list", classes="pk-list")
            
            # Column 3: PII COLUMNS - split upper/lower
            with Vertical(id="pii-column", classes="column"):
                yield Label("🔒 PII COLUMNS >P", classes="column-header")
                # Upper: Current PII rules
                with Vertical(classes="panel-upper"):
                    yield Static("選擇資料表以查看 PII 規則", id="pii-display", classes="no-rule")
                # Lower: All Columns
                with Vertical(classes="panel-lower"):
                    yield Label("📋 All Columns", classes="panel-lower-header")
                    with ScrollableContainer(id="col-scroll", classes="metadata-scroll"):
                        yield Static("", id="column-list", classes="column-list")
        
        yield Footer()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update side panels when highlighted item changes"""
        if event.item and isinstance(event.item, TableItem):
            self.current_table = event.item.table_name
            self._update_side_panels()

    def _load_table_metadata(self, table_name: str) -> None:
        """Lazy load columns and primary keys for the selected table"""
        if table_name in self.table_columns_cache:
            return  # Already cached
        
        if self.inspector:
            try:
                # Get columns
                columns = self.inspector.get_columns(table_name)
                self.table_columns_cache[table_name] = [c['name'] for c in columns]
                
                # Get primary keys
                pk_constraint = self.inspector.get_pk_constraint(table_name)
                self.table_pk_cache[table_name] = pk_constraint.get('constrained_columns', [])
            except Exception as e:
                self.notify(f"⚠️ 無法載入 {table_name} 元數據: {e}", severity="warning")
                self.table_columns_cache[table_name] = []
                self.table_pk_cache[table_name] = []
        else:
            # Demo mode - generate mock data
            self.table_columns_cache[table_name] = [f"column_{i}" for i in range(1, 16)]
            self.table_pk_cache[table_name] = ["id", "seq_no"]

    def _update_side_panels(self) -> None:
        """Update filter and PII display panels for current table"""
        if not self.current_table:
            return
        
        # Load table metadata if not cached
        self._load_table_metadata(self.current_table)
        
        # Update Filter Display (upper section)
        filter_display = self.query_one("#filter-display", Static)
        filter_rule = LARGE_TABLE_FILTERS.get(self.current_table)
        if filter_rule:
            filter_display.update(f"✅ WHERE:\n{filter_rule}")
            filter_display.remove_class("no-rule")
            filter_display.add_class("has-rule")
        else:
            filter_display.update("無篩選條件\n\n按 F 新增")
            filter_display.remove_class("has-rule")
            filter_display.add_class("no-rule")
        
        # Update Primary Keys Display (lower section)
        pk_list = self.query_one("#pk-list", Static)
        pks = self.table_pk_cache.get(self.current_table, [])
        if pks:
            pk_text = "\n".join([f"  • {pk}" for pk in pks])
            pk_list.update(pk_text)
        else:
            pk_list.update("  (無主鍵)")
        
        # Update PII Display (upper section)
        pii_display = self.query_one("#pii-display", Static)
        pii_rules = SENSITIVE_COLUMNS.get(self.current_table)
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
        
        # Update All Columns Display (lower section)
        column_list = self.query_one("#column-list", Static)
        columns = self.table_columns_cache.get(self.current_table, [])
        if columns:
            col_text = "\n".join([f"  • {col}" for col in columns])
            column_list.update(col_text)
        else:
            column_list.update("  (無欄位資訊)")

    def action_toggle_current(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        if list_view.highlighted_child:
            list_view.highlighted_child.toggle()

    def action_select_all(self) -> None:
        self.all_selected = not self.all_selected
        list_view = self.query_one("#table-list", ListView)
        target_state = self.all_selected
        
        for item in list_view.children:
            if isinstance(item, TableItem):
                if item.checked != target_state:
                    item.toggle()
        
        self.notify(f"{'已全選' if self.all_selected else '已取消全選'}")

    def action_edit_filter(self) -> None:
        """Open filter editor for current table"""
        if not self.current_table:
            self.notify("請先選擇資料表", severity="warning")
            return
        
        current_filter = LARGE_TABLE_FILTERS.get(self.current_table, "")
        
        def on_filter_result(result: str | None) -> None:
            if result is not None:
                if result:
                    LARGE_TABLE_FILTERS[self.current_table] = result
                    self.notify(f"✅ 已更新 {self.current_table} 的篩選條件")
                else:
                    # Empty string means remove the filter
                    if self.current_table in LARGE_TABLE_FILTERS:
                        del LARGE_TABLE_FILTERS[self.current_table]
                        self.notify(f"🗑️ 已移除 {self.current_table} 的篩選條件")
                self.configs_modified = True
                self._update_side_panels()
        
        self.push_screen(FilterEditorScreen(self.current_table, current_filter), on_filter_result)

    def action_edit_pii(self) -> None:
        """Open PII editor for current table"""
        if not self.current_table:
            self.notify("請先選擇資料表", severity="warning")
            return
        
        current_rules = SENSITIVE_COLUMNS.get(self.current_table, {})
        
        def on_pii_result(result: dict | None) -> None:
            if result is not None:
                if result:
                    SENSITIVE_COLUMNS[self.current_table] = result
                    self.notify(f"✅ 已更新 {self.current_table} 的 PII 規則")
                else:
                    # Empty dict means remove the rules
                    if self.current_table in SENSITIVE_COLUMNS:
                        del SENSITIVE_COLUMNS[self.current_table]
                        self.notify(f"🗑️ 已移除 {self.current_table} 的 PII 規則")
                self.configs_modified = True
                self._update_side_panels()
        
        self.push_screen(PIIEditorScreen(self.current_table, current_rules), on_pii_result)

    def action_save_configs(self) -> None:
        """Save modified configs to JSON files"""
        try:
            # Save LARGE_TABLE_FILTERS
            with open('large_table_filters.json', 'w', encoding='utf-8') as f:
                json.dump(LARGE_TABLE_FILTERS, f, ensure_ascii=False, indent=2)
            
            # Save SENSITIVE_COLUMNS - convert tuples to lists for JSON
            sc_json = {}
            for table, rules in SENSITIVE_COLUMNS.items():
                sc_json[table] = {}
                for col, (func_name, seed_col) in rules.items():
                    sc_json[table][col] = [func_name, seed_col]
            
            with open('sensitive_columns.json', 'w', encoding='utf-8') as f:
                json.dump(sc_json, f, ensure_ascii=False, indent=2)
            
            self.configs_modified = False
            self.notify("✅ 設定已儲存至 large_table_filters.json 和 sensitive_columns.json")
        except Exception as e:
            self.notify(f"❌ 儲存失敗: {e}", severity="error")

    def action_initiate_confirm(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        selected_temp = [
            item.table_name for item in list_view.children 
            if isinstance(item, TableItem) and item.checked
        ]
        
        if not selected_temp:
            self.notify("請至少選擇一個資料表！", severity="error")
            return

        # Warn if configs modified but not saved
        warning = ""
        if self.configs_modified:
            warning = "\n\n⚠️ 有未儲存的設定變更！"

        msg = f"已選擇 {len(selected_temp)} 個資料表。{warning}\n\n確定要開始嗎？"
        
        def check_confirm(is_confirmed: bool) -> None:
            if is_confirmed:
                self.selected_tables = selected_temp
                self.exit(self.selected_tables)
        
        self.push_screen(ConfirmScreen(msg), check_confirm)

# --- Core Logic ---

def get_db_connection(args=None):
    """Setup source and target database connections based on configuration"""
    print("\n--- 設定資料庫連線 ---")

    def get_conf(arg_name, env_name, default_val):
        # Priority: CLI Arg > Env Var > Default
        if args and getattr(args, arg_name, None):
            return getattr(args, arg_name)
        return os.environ.get(env_name, default_val)
    
    # Source Database Configuration
    src_config = {
        'driver': 'ODBC Driver 18 for SQL Server',
        'server': get_conf('src_server', 'SRC_DB_SERVER', '172.22.1.34'),
        'database': get_conf('src_database', 'SRC_DB_NAME', 'hrm'),
        'uid': get_conf('src_uid', 'SRC_DB_UID', 'yr3158'),
        'pwd': get_conf('src_pwd', 'SRC_DB_PWD', 'Vita0309'),
        'trust_server_certificate': 'yes'
    }

    # Target Database Configuration
    tgt_config = {
        'driver': 'ODBC Driver 18 for SQL Server',
        'server': get_conf('tgt_server', 'TGT_DB_SERVER', 'localhost'),
        'database': get_conf('tgt_database', 'TGT_DB_NAME', 'hrm'),
        'uid': get_conf('tgt_uid', 'TGT_DB_UID', 'sa'),
        'pwd': get_conf('tgt_pwd', 'TGT_DB_PWD', 'No@KeyTakeaway'),
        'trust_server_certificate': 'yes'
    }

    
    # Construct Connection Strings
    # Format: mssql+pyodbc://uid:pwd@server/database?driver=...
    
    import urllib.parse

    def build_conn_str(cfg):
        # Encode password to handle special characters (e.g., '@')
        encoded_pwd = urllib.parse.quote_plus(cfg['pwd'])
        return (f"mssql+pyodbc://{cfg['uid']}:{encoded_pwd}@{cfg['server']}/{cfg['database']}"
                f"?driver={cfg['driver'].replace(' ', '+')}&TrustServerCertificate={cfg['trust_server_certificate']}")

    src_conn_str = build_conn_str(src_config)
    tgt_conn_str = build_conn_str(tgt_config)

    logger.info(f"來源 (Source): {src_config['server']} ({src_config['uid']})")
    logger.info(f"目標 (Target): {tgt_config['server']} ({tgt_config['uid']})")
    
    try:
        src_engine = create_engine(src_conn_str)
        # Verify source connection
        with src_engine.connect() as conn:
            logger.info("✅ 來源資料庫連線成功！")
            
        tgt_engine = create_engine(tgt_conn_str)
        # Verify target connection
        with tgt_engine.connect() as conn:
            logger.info("✅ 目標資料庫連線成功！")
            
        return src_engine, tgt_engine
        
    except Exception as e:
        logger.error(f"❌ 資料庫連線失敗: {e}")
        return None, None

def apply_anonymization(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Apply anonymization rules to the DataFrame"""
    # Normalize SENSITIVE_COLUMNS keys for case-insensitive lookup
    # Create a mapping of upper_case_table_name -> actual_config_key
    sc_map = {k.upper(): k for k in SENSITIVE_COLUMNS.keys()}
    
    table_key = table_name
    if table_name.upper() in sc_map:
        table_key = sc_map[table_name.upper()]
    else:
    # Not found
        # logger.debug(f"DEBUG: Table {table_name} not in sensitive list (Checked against: {list(sc_map.keys())})")
        return df
    
    logger.debug(f"DEBUG: Applying rules for {table_name} (found config key: {table_key})")
    rules = SENSITIVE_COLUMNS[table_key]
    for col, (func_name, seed_col) in rules.items():
        if col in df.columns:
            logger.debug(f"  -> Processing column: {col} with {func_name} (seed: {seed_col})")
            # Get the function from global scope
            func = globals()[func_name]
            
            # Apply row by row (performance impact but necessary for dependencies like emp_no)
            # Optimization: Vectorize if possible, but custom logic usually needs apply
            if seed_col:
                if seed_col not in df.columns:
                    logger.warning(f"  ⚠️ Warning: Seed column '{seed_col}' not found in {table_name}. Skipping...")
                    continue
                
                # Debug sample before
                if not df.empty:
                    sample_row = df.iloc[0]
                    logger.debug(f"  DEBUG: Sample Before - {col}: '{sample_row[col]}', {seed_col}: '{sample_row[seed_col]}'")

                # Salted Seed Logic
                df[col] = df.apply(lambda row: func(row[col], f"{row[seed_col]}_{DATE_SALT}"), axis=1)

                # Debug sample after
                if not df.empty:
                    logger.debug(f"  DEBUG: Sample After  - {col}: '{df.iloc[0][col]}'")
            else:
                df[col] = df[col].apply(lambda x: func(x))
        else:
            logger.debug(f"  DEBUG: Column {col} not found in dataframe columns: {df.columns.tolist()}")
                
    return df

def run_replication(args=None):
    # 1. 建立連線
    source_engine, target_engine = get_db_connection(args)

    if source_engine:
        try:
            initialize_name_data(source_engine)
        except Exception as e:
            logger.warning(f"⚠️ Warning: Failed to initialize name data: {e}")
    
    if not source_engine or not target_engine:
        logger.warning("⚠️ 無法建立有效連線，切換至 [Demo 模式]...")
        # 模擬資料表清單
        mock_tables = [f"TABLE_{i:03d}" for i in range(1, 251)] 
        mock_tables.extend(LARGE_TABLE_FILTERS.keys()) 
        mock_tables.extend(SENSITIVE_COLUMNS.keys())
        all_tables = sorted(list(set(mock_tables)))
        insp = None  # No inspector in demo mode
    else:
        # 從真實資料庫取得資料表清單
        logger.info("正在讀取資料表清單...")
        insp = inspect(source_engine)
        all_tables = sorted(insp.get_table_names())

    # 2. 啟動 TUI 選擇 (pass inspector for metadata loading)
    app = TableSelector(all_tables, inspector=insp)
    selected_tables = app.run()

    if not selected_tables:
        logger.info("未選擇任何資料表，程式結束。")
        return

    logger.info(f"\n已選擇 {len(selected_tables)} 個資料表，準備開始複製...\n")
    
    # 3. 處理複製
    for table in selected_tables:
        logger.info(f"處理資料表: {table}")
        
        # 檢查是否有篩選條件
        where_clause = LARGE_TABLE_FILTERS.get(table)
        if where_clause:
            logger.info(f"  -> 套用篩選條件: {where_clause}")
            query = f"SELECT * FROM {table} WHERE {where_clause}"
            count_query = f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
        else:
            query = f"SELECT * FROM {table}"
            count_query = f"SELECT COUNT(*) FROM {table}"
            
        try:
            if source_engine and target_engine:
                # 真實執行
                # 計算總筆數 (使用 pandas read_sql 可能較慢，優化可改用 text 直接查詢)
                #total_count = pd.read_sql(count_query, source_engine).iloc[0, 0] 
                # 使用 connection 執行 scalar 查詢優化效能
                with source_engine.connect() as conn:
                    total_count = conn.execute(text(count_query)).scalar()

                chunk_size = 5000
                with tqdm(total=total_count, desc=f"Copying {table}", unit="rows") as pbar:
                    for chunk in pd.read_sql(query, source_engine, chunksize=chunk_size):
                        # 套用去識別化
                        chunk = apply_anonymization(chunk, table)
                        
                        # Fix encoding issues for Chinese characters:
                        # Map all object (string) columns to NVARCHAR explicitly
                        # Include both 'object' and 'str' to support Pandas 3.x+ string dtypes
                        dtype_map = {c: NVARCHAR for c in chunk.select_dtypes(include=['object', 'str']).columns}

                        # 寫入目標資料庫
                        chunk.to_sql(table, target_engine, if_exists='append', index=False, dtype=dtype_map)
                        pbar.update(len(chunk))
            else:
                # 模擬執行
                total_rows = 15000 
                chunk_size = 5000
                with tqdm(total=total_rows, desc=f"Copying {table} (Mock)", unit="rows") as pbar:
                    for _ in range(0, total_rows, chunk_size):
                        time.sleep(0.1) 
                        pbar.update(chunk_size)

        except Exception as e:
            logger.error(f"❌ 處理 {table} 時發生錯誤: {e}")
            continue
    
    logger.info("\n所有作業完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HRM Database Replicator")
    
    # Mode flags
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies and exit")
    parser.add_argument("--demo", action="store_true", help="Run in demo/simulation mode")
    
    # Source DB Connection Args
    parser.add_argument("--src-server", help="Source Database Server IP/Hostname")
    parser.add_argument("--src-database", help="Source Database Name")
    parser.add_argument("--src-uid", help="Source Database User ID")
    parser.add_argument("--src-pwd", help="Source Database Password")

    # Target DB Connection Args
    parser.add_argument("--tgt-server", help="Target Database Server IP/Hostname")
    parser.add_argument("--tgt-database", help="Target Database Name")
    parser.add_argument("--tgt-uid", help="Target Database User ID")
    parser.add_argument("--tgt-pwd", help="Target Database Password")


    args = parser.parse_args()

    # Check arguments
    if args.check_deps:
        print("Dependencies OK")
        sys.exit(0)
        
    if not args.demo:
        # Implicit demo check removed, rely on get_db_connection handling or explicit demo flag logic if needed.
        # Original code prompted for --demo if not provided. Keeping behavior similar but more direct.
        pass

    # Pass parsed arguments to the main logic
    run_replication(args)
