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
from textual.widgets import Header, Footer, ListView, ListItem, Label, Button
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
from textual.screen import ModalScreen


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

class TableSelector(App):
    """Textual App for selecting tables with virtual scrolling"""
    
    CSS = """
    ListView {
        height: 1fr;
        border: solid green;
    }
    .selected {
        background: $accent;
    }
    """

    BINDINGS = [

        Binding("a", "select_all", "全選/取消全選"),
        Binding("space", "toggle_current", "選取/取消"),
        Binding("g", "initiate_confirm", "確認並開始"),
        ("q", "quit", "離開"),
    ]


    def __init__(self, table_names: List[str]):
        super().__init__()
        self.table_names = table_names
        self.selected_tables = []
        self.all_selected = False

    def on_mount(self) -> None:
        self.query_one("#table-list", ListView).focus()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"偵測到 {len(self.table_names)} 個資料表。請使用上下鍵移動，滑鼠點擊或空白鍵選取，按 G 確認。", classes="info")
        
        # 使用 ListView 實現虛擬滾動
        items = [TableItem(name) for name in self.table_names]
        yield ListView(*items, id="table-list")
        
        yield Footer()

    def action_toggle_current(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        if list_view.highlighted_child:
            list_view.highlighted_child.toggle()

    def action_select_all(self) -> None:
        self.all_selected = not self.all_selected
        list_view = self.query_one("#table-list", ListView)
        target_state = self.all_selected
        
        # Optimize: Avoid re-rendering if possible, but basic update is fine for 200 items
        for item in list_view.children:
            if isinstance(item, TableItem):
                if item.checked != target_state:
                    item.toggle()
        
        self.notify(f"{'已全選' if self.all_selected else '已取消全選'}")

    def action_initiate_confirm(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        selected_temp = [
            item.table_name for item in list_view.children 
            if isinstance(item, TableItem) and item.checked
        ]
        
        if not selected_temp:
            self.notify("請至少選擇一個資料表！", severity="error")
            return

        msg = f"已選擇 {len(selected_temp)} 個資料表。\n\n確定要開始嗎？"
        
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
    else:
        # 從真實資料庫取得資料表清單
        logger.info("正在讀取資料表清單...")
        insp = inspect(source_engine)
        all_tables = sorted(insp.get_table_names())

    # 2. 啟動 TUI 選擇
    app = TableSelector(all_tables)
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
