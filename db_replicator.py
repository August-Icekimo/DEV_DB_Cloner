"""
db_replicator.py — Thin orchestration layer.

Wires together:  config_manager  ·  clone_engine  ·  tui_screens  ·  data_anonymizer
All DB / DDL logic lives in clone_engine.py.
All Textual TUI classes live in tui_screens.py.
"""
import sys
import re
import importlib
import time
from datetime import datetime
import pandas as pd
import os
import argparse
import json
from typing import List, Dict, Optional
import logging

# ---------------------------------------------------------------------------
# Constants & logging setup  (must happen before any module import)
# ---------------------------------------------------------------------------

CURRENT_DATE_STR      = datetime.now().strftime('%Y%m%d')
LOG_FILENAME          = f"{CURRENT_DATE_STR}_Clone.log"
RETRY_SCRIPT_FILENAME = f"{CURRENT_DATE_STR}_Clone_Retry.sql"
DATE_SALT             = CURRENT_DATE_STR

logger = logging.getLogger("DB_Replicator")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(LOG_FILENAME, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(message)s'))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Runtime-populated globals (set by profile_to_payload or run_replication)
LARGE_TABLE_FILTERS: Dict[str, str] = {}
SENSITIVE_COLUMNS:   Dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES = {
    'sqlalchemy': 'SQLAlchemy',
    'pandas':     'pandas',
    'pymssql':    'pymssql',
    'tqdm':       'tqdm',
    'textual':    'textual',
}

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

# ---------------------------------------------------------------------------
# Imports from sibling modules (safe after dependency check)
# ---------------------------------------------------------------------------

from sqlalchemy import inspect
from sqlalchemy.types import NVARCHAR
from tqdm import tqdm

from data_anonymizer import (
    obfuscate_name, anonymize_id, obfuscate_address,
    initialize_name_data, obfuscate_spouse_name, obfuscate_phone,
    clear_content, obfuscate_family_name, VALID_ANON_FUNCTIONS,
)

from config_manager import config_mgr

from clone_engine import (
    create_target_table_from_source,
    clone_views,
    clone_sps_and_functions,
    clone_triggers,
    write_retry_script,
    get_db_connection,
    fetch_all_views,
    fetch_all_sps,
    fetch_all_functions,
    fetch_all_triggers,
)

from tui_screens import ProjectSelector, TableSelector

# ---------------------------------------------------------------------------
# Anonymization dispatcher
# ---------------------------------------------------------------------------

def apply_anonymization(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    sc_map = {k.upper(): k for k in SENSITIVE_COLUMNS.keys()}
    if table_name.upper() not in sc_map:
        return df

    table_key = sc_map[table_name.upper()]
    logger.debug(f"DEBUG: Applying rules for {table_name} (found config key: {table_key})")
    rules = SENSITIVE_COLUMNS[table_key]

    for col, (func_name, seed_col) in rules.items():
        if col not in df.columns:
            logger.debug(f"  DEBUG: Column {col} not found in dataframe columns: {df.columns.tolist()}")
            continue

        logger.debug(f"  -> Processing column: {col} with {func_name} (seed: {seed_col})")
        func = globals()[func_name]

        # Multi-column seed spec (colon-delimited): "emp_col:rel_col:sort_col"
        if seed_col and ':' in seed_col:
            parts    = seed_col.split(':')
            emp_col  = parts[0]
            rel_col  = parts[1]
            sort_col = parts[2] if len(parts) > 2 else None

            missing = [c for c in [emp_col, rel_col] if c not in df.columns]
            if missing:
                logger.warning(f"  ⚠️ Composite seed columns not found for {col}: {missing}. Skipping...")
                continue

            sorted_df  = df.sort_values([emp_col, rel_col, sort_col] if sort_col and sort_col in df.columns
                                         else [emp_col, rel_col])
            member_idx = sorted_df.groupby([emp_col, rel_col]).cumcount()

            df['__composite_seed__'] = (
                df[emp_col].astype(str) + '|' +
                df[rel_col].astype(str) + '|' +
                member_idx.reindex(df.index).astype(str)
            )
            if not df.empty:
                logger.debug(f"  DEBUG: Sample composite_seed: '{df['__composite_seed__'].iloc[0]}'")

            df[col] = df.apply(lambda row: func(row[col], row['__composite_seed__']), axis=1)
            df.drop(columns=['__composite_seed__'], inplace=True)

            if not df.empty:
                logger.debug(f"  DEBUG: Sample After - {col}: '{df[col].iloc[0]}'")
            continue

        # Single-column seed
        if seed_col:
            if seed_col not in df.columns:
                logger.warning(f"  ⚠️ Warning: Seed column '{seed_col}' not found in {table_name}. Skipping...")
                continue
            if not df.empty:
                sample_row = df.iloc[0]
                logger.debug(f"  DEBUG: Sample Before - {col}: '{sample_row[col]}', {seed_col}: '{sample_row[seed_col]}'")

            df[col] = df.apply(lambda row: func(row[col], f"{row[seed_col]}_{DATE_SALT}"), axis=1)

            if not df.empty:
                logger.debug(f"  DEBUG: Sample After  - {col}: '{df.iloc[0][col]}'")
        else:
            df[col] = df[col].apply(lambda x: func(x))

    return df

# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def load_and_validate_profile(path: str) -> dict:
    if not os.path.exists(path):
        logger.error(f"❌ 找不到設定檔：{path}")
        sys.exit(1)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    except Exception as e:
        logger.error(f"❌ 設定檔 JSON 格式錯誤：{e}")
        sys.exit(1)

    version = profile.get("profile_version")
    if version != "1.0":
        logger.error(f"❌ 不支援的 Profile 版本：{version}，目前僅支援 1.0")
        sys.exit(1)

    if "objects" not in profile:
        logger.error("❌ 設定檔缺少必填欄位：objects")
        sys.exit(1)

    pii_rules = profile.get("pii_rules", {})
    for table, rules in pii_rules.items():
        for col, rule in rules.items():
            func_name = rule[0] if isinstance(rule, list) else None
            if func_name and func_name not in VALID_ANON_FUNCTIONS:
                logger.error(f"❌ 未知的去敏化函數：{func_name} (於 {table}.{col})")
                sys.exit(1)

    return profile


def profile_to_payload(profile: dict) -> dict:
    global LARGE_TABLE_FILTERS, SENSITIVE_COLUMNS

    objects = profile.get("objects", {})
    payload = {
        "tables":    objects.get("tables", []),
        "views":     objects.get("views", []),
        "sps":       objects.get("sps", []),
        "functions": objects.get("functions", []),
        "triggers":  objects.get("triggers", []),
    }
    # Side-effect: populate globals so _execute_replication picks them up.
    LARGE_TABLE_FILTERS = profile.get("filters", {})
    SENSITIVE_COLUMNS   = profile.get("pii_rules", {})
    return payload

# ---------------------------------------------------------------------------
# Core replication logic
# ---------------------------------------------------------------------------

def _execute_replication(payload, source_engine, target_engine, src_db, tgt_db):
    selected_tables   = list(payload.get("tables",    []))
    selected_views    = list(payload.get("views",     []))
    selected_funcs    = list(payload.get("functions", []))
    selected_sps      = list(payload.get("sps",       []))
    selected_triggers = list(payload.get("triggers",  []))

    logger.info("\n準備開始複製...\n")

    # Phase 1: Tables
    for table in selected_tables:
        logger.info(f"處理資料表: {table}")

        where_clause = LARGE_TABLE_FILTERS.get(table)
        if where_clause:
            logger.info(f"  -> 套用篩選條件: {where_clause}")
            query = f"SELECT * FROM {table} WHERE {where_clause}"
            count_where = re.split(r'\s+ORDER\s+BY\s+', where_clause, flags=re.IGNORECASE)[0]
            count_query = f"SELECT COUNT(*) FROM {table} WHERE {count_where}"
        else:
            query       = f"SELECT * FROM {table}"
            count_query = f"SELECT COUNT(*) FROM {table}"

        try:
            if source_engine and target_engine:
                schema_ok = create_target_table_from_source(source_engine, target_engine, table)
                if not schema_ok:
                    logger.warning(
                        f"  🚨 [{table}] DROP 與 TRUNCATE 均失敗 — 將直接 APPEND 至現有資料表！"
                        f"\n     ⚠️  若表中已有資料，本次複製將造成資料重複累加（Double Data）。"
                        f"\n     請手動確認 target [{table}] 是否需要先清空。"
                    )
                with source_engine.connect() as conn:
                    total_count = conn.exec_driver_sql(count_query).scalar()

                chunk_size = 5000
                with tqdm(total=total_count, desc=f"Copying {table}", unit="rows") as pbar:
                    for chunk in pd.read_sql(query, source_engine, chunksize=chunk_size):
                        chunk    = apply_anonymization(chunk, table)
                        dtype_map = {c: NVARCHAR for c in chunk.select_dtypes(include=['object', 'str']).columns}
                        chunk.to_sql(table, target_engine, if_exists='append', index=False, dtype=dtype_map)
                        pbar.update(len(chunk))
            else:
                total_rows = 15000
                chunk_size = 5000
                with tqdm(total=total_rows, desc=f"Copying {table} (Mock)", unit="rows") as pbar:
                    for _ in range(0, total_rows, chunk_size):
                        time.sleep(0.1)
                        pbar.update(chunk_size)

        except Exception as e:
            logger.error(f"❌ 處理 {table} 時發生錯誤: {e}")
            continue

    retry_items: list = []

    if source_engine and target_engine:
        if selected_views:
            clone_views(selected_views, source_engine, target_engine, src_db, tgt_db,
                        retry_items=retry_items)
        if selected_funcs:
            clone_sps_and_functions(selected_funcs, source_engine, target_engine, src_db, tgt_db,
                                    is_func=True, retry_items=retry_items)
        if selected_sps:
            clone_sps_and_functions(selected_sps, source_engine, target_engine, src_db, tgt_db,
                                    is_func=False, retry_items=retry_items)
        if selected_triggers:
            clone_triggers(selected_triggers, source_engine, target_engine, src_db, tgt_db,
                           retry_items=retry_items)

        write_retry_script(retry_items, src_db, tgt_db)
    else:
        logger.info("Demo 模式：略過 View / SP / Function / Trigger 的實際複製")

    logger.info("\n所有作業完成！")


def _validate_headless_connections(args):
    required = [
        ('src_server',   'SRC_DB_SERVER'), ('src_database', 'SRC_DB_NAME'),
        ('src_uid',      'SRC_DB_UID'),    ('src_pwd',      'SRC_DB_PWD'),
        ('tgt_server',   'TGT_DB_SERVER'), ('tgt_database', 'TGT_DB_NAME'),
        ('tgt_uid',      'TGT_DB_UID'),    ('tgt_pwd',      'TGT_DB_PWD'),
    ]
    missing = [
        f"--{arg.replace('_', '-')} 或 {env}"
        for arg, env in required
        if not (getattr(args, arg, None) or os.environ.get(env))
    ]
    if missing:
        logger.error("❌ Headless 模式需明確指定連線參數：")
        for m in missing:
            logger.error(f"   - 缺失: {m}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_replication(args=None):
    # --- Headless path ---
    if args and args.deploy_profile:
        logger.info(f"🚀 Headless 模式：載入設定檔 {args.deploy_profile}")
        profile = load_and_validate_profile(args.deploy_profile)

        if args.demo:
            logger.warning("⚠️ Headless 模式運行於 [Demo 模式]")
            source_engine, target_engine, src_db, tgt_db = None, None, "demo_src", "demo_tgt"
        else:
            _validate_headless_connections(args)
            source_engine, target_engine, src_db, tgt_db = get_db_connection(args, project=None)
            if not source_engine or not target_engine:
                logger.error("❌ Headless 模式連線失敗，請檢查參數")
                sys.exit(1)

        payload     = profile_to_payload(profile)
        proj_name   = profile.get("project_name")
        target_proj = config_mgr.get_project_by_name(proj_name)
        if target_proj:
            logger.info(f"✅ 找到對應專案 '{proj_name}'，使用其姓名來源設定")
            initialize_name_data(source_engine,
                                 source_type=target_proj.name_source_type,
                                 source_value=target_proj.name_source_value)
        else:
            logger.info(f"ℹ️ 未找到專案 '{proj_name}'，使用預設姓名資料")
            initialize_name_data(source_engine)

        _execute_replication(payload, source_engine, target_engine, src_db, tgt_db)
        logger.info("🏁 Headless 部署完成")
        return

    # --- TUI path ---
    cli_demo = args and getattr(args, 'demo', False)

    while True:
        proj_app   = ProjectSelector()
        project_id = proj_app.run()

        if not project_id:
            logger.info("未選擇專案，結束。")
            return

        project = config_mgr.get_project_by_id(project_id)
        _, filters, pii_rules, name_source = config_mgr.get_project_config(project_id)

        global SENSITIVE_COLUMNS, LARGE_TABLE_FILTERS
        SENSITIVE_COLUMNS   = pii_rules
        LARGE_TABLE_FILTERS = filters

        conn_project = None if cli_demo else project
        source_engine, target_engine, src_db, tgt_db = get_db_connection(args, conn_project)

        proj_cfg = config_mgr.get_connection_config(project_id)
        is_demo  = cli_demo or (proj_cfg.get("demo_mode") and not source_engine)

        if not source_engine and not is_demo:
            msg = "❌ 無法建立資料庫連線，請至連線設定 (L) 修正"
            logger.warning(msg)
            print(f"\n{msg}\n")
            continue

        if source_engine:
            try:
                initialize_name_data(source_engine,
                                     source_type=name_source['type'],
                                     source_value=name_source['value'])
            except Exception as e:
                logger.warning(f"⚠️ Warning: Failed to initialize name data: {e}")
        else:
            initialize_name_data(None)

        if not source_engine:
            logger.warning("⚠️ 進入 [Demo 模式]")
            mock_tables  = [f"TABLE_{i:03d}" for i in range(1, 251)]
            mock_tables.extend(LARGE_TABLE_FILTERS.keys())
            mock_tables.extend(SENSITIVE_COLUMNS.keys())
            all_tables   = sorted(set(mock_tables))
            objects_dict = {
                "TABLE":    all_tables,
                "VIEW":     [f"VW_DEMO_{i}" for i in range(1, 10)],
                "SP":       [f"USP_DEMO_{i}" for i in range(1, 10)],
                "FUNCTION": [f"UDF_DEMO_{i}" for i in range(1, 10)],
                "TRIGGER":  [f"TRG_DEMO_{i}" for i in range(1, 10)],
            }
            insp = None
        else:
            logger.info("正在讀取資料庫物件清單...")
            insp       = inspect(source_engine)
            all_tables = sorted(insp.get_table_names())
            objects_dict = {
                "TABLE":    all_tables,
                "VIEW":     fetch_all_views(source_engine),
                "SP":       fetch_all_sps(source_engine),
                "FUNCTION": fetch_all_functions(source_engine),
                "TRIGGER":  fetch_all_triggers(source_engine),
            }

        app     = TableSelector(project_id, objects_dict, inspector=insp)
        payload = app.run()

        if payload == "__BACK_TO_PROJECT__":
            logger.info("返回專案選擇...")
            continue

        if not isinstance(payload, dict):
            logger.info("未選擇任何物件，程式結束。")
            return

        _, filters, pii_rules, _ = config_mgr.get_project_config(project_id)
        SENSITIVE_COLUMNS   = pii_rules
        LARGE_TABLE_FILTERS = filters

        _execute_replication(payload, source_engine, target_engine, src_db, tgt_db)
        input("\n請按 Enter 鍵返回首頁...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HRM Database Replicator")

    parser.add_argument("--check-deps",     action="store_true",
                        help="Check dependencies and exit")
    parser.add_argument("--demo",           action="store_true",
                        help="Run in demo/simulation mode")
    parser.add_argument("--deploy-profile", metavar="PATH",
                        help="Deploy Profile JSON 路徑，指定後跳過 TUI 直接執行批次部署")

    parser.add_argument("--src-server",   help="Source Database Server IP/Hostname")
    parser.add_argument("--src-database", help="Source Database Name")
    parser.add_argument("--src-uid",      help="Source Database User ID")
    parser.add_argument("--src-pwd",      help="Source Database Password")

    parser.add_argument("--tgt-server",   help="Target Database Server IP/Hostname")
    parser.add_argument("--tgt-database", help="Target Database Name")
    parser.add_argument("--tgt-uid",      help="Target Database User ID")
    parser.add_argument("--tgt-pwd",      help="Target Database Password")

    args = parser.parse_args()

    if args.check_deps:
        print("Dependencies OK")
        sys.exit(0)

    run_replication(args)
