"""
clone_engine.py — DB fetch, DDL preprocessing, and clone execution.
No Textual dependency.
"""
import re
import os
import urllib.parse
import logging
from datetime import datetime
from typing import List, Dict

from sqlalchemy import create_engine, inspect

from config_manager import config_mgr

logger = logging.getLogger("DB_Replicator")

_DATE_STR             = datetime.now().strftime('%Y%m%d')
RETRY_SCRIPT_FILENAME = f"{_DATE_STR}_Clone_Retry.sql"


# ---------------------------------------------------------------------------
# Object list fetchers
# ---------------------------------------------------------------------------

def fetch_all_views(engine) -> List[str]:
    insp = inspect(engine)
    return sorted(insp.get_view_names())

def fetch_all_sps(engine) -> List[str]:
    query = "SELECT name FROM sys.objects WHERE type = 'P' AND is_ms_shipped = 0 ORDER BY name"
    with engine.connect() as conn:
        return [row[0] for row in conn.exec_driver_sql(query)]

def fetch_all_functions(engine) -> List[str]:
    query = "SELECT name FROM sys.objects WHERE type IN ('FN', 'IF', 'TF') AND is_ms_shipped = 0 ORDER BY name"
    with engine.connect() as conn:
        return [row[0] for row in conn.exec_driver_sql(query)]

def fetch_all_triggers(engine) -> List[str]:
    query = "SELECT name FROM sys.triggers WHERE is_ms_shipped = 0 ORDER BY name"
    with engine.connect() as conn:
        return [row[0] for row in conn.exec_driver_sql(query)]


# ---------------------------------------------------------------------------
# Table schema creation
# ---------------------------------------------------------------------------

def create_target_table_from_source(src_engine, tgt_engine, table_name: str) -> bool:
    """
    Query sys.columns on source and CREATE the table on target with correct types.
    IDENTITY constraints are intentionally omitted so we can INSERT source values directly.
    Returns True on success, False on failure.
    """
    safe_str = table_name.replace("'", "''")
    safe_id  = table_name.replace("]", "]]")

    query = f"""
        SELECT
            c.name,
            tp.name AS type_name,
            CASE
                -- varchar/char: 放寬 2x 以容納 CP950→UTF-8 最大膨脹
                -- 罕見字/補充字集 (U+20000+) 及 Latin-1 誤讀情境均為 2x，1.5x 不足
                WHEN tp.name IN ('varchar','char')
                    THEN CASE WHEN c.max_length = -1 THEN 'MAX'
                              WHEN c.max_length * 2 > 8000 THEN 'MAX'
                              ELSE CAST(c.max_length * 2 AS VARCHAR) END
                WHEN tp.name IN ('varbinary','binary')
                    THEN CASE WHEN c.max_length = -1 THEN 'MAX'
                              ELSE CAST(c.max_length AS VARCHAR) END
                WHEN tp.name IN ('nvarchar','nchar')
                    THEN CASE WHEN c.max_length = -1 THEN 'MAX'
                              ELSE CAST(c.max_length / 2 AS VARCHAR) END
                WHEN tp.name IN ('decimal','numeric')
                    THEN CAST(c.precision AS VARCHAR) + ',' + CAST(c.scale AS VARCHAR)
                WHEN tp.name IN ('datetime2','datetimeoffset','time')
                    THEN CAST(c.scale AS VARCHAR)
                ELSE NULL
            END AS type_params,
            c.is_nullable
        FROM sys.columns c
        JOIN sys.types tp ON c.user_type_id = tp.user_type_id
        WHERE c.object_id = OBJECT_ID('{safe_str}')
        ORDER BY c.column_id
    """
    try:
        with src_engine.connect() as conn:
            rows = conn.exec_driver_sql(query).fetchall()
        if not rows:
            logger.warning(f"  ⚠️ 無法從 source 取得 {table_name} 的欄位資訊，將由 pandas 自動建立結構")
            return False

        col_defs = []
        for col_name, type_name, type_params, is_nullable in rows:
            col_type = f"{type_name}({type_params})" if type_params else type_name
            null_clause = "NULL" if is_nullable else "NOT NULL"
            safe_col = col_name.replace("]", "]]")
            col_defs.append(f"    [{safe_col}] {col_type} {null_clause}")

        create_ddl = f"CREATE TABLE [{safe_id}] (\n" + ",\n".join(col_defs) + "\n)"
        drop_ddl   = f"IF OBJECT_ID('{safe_str}', 'U') IS NOT NULL DROP TABLE [{safe_id}]"

        try:
            with tgt_engine.begin() as conn:
                conn.exec_driver_sql(drop_ddl)
                conn.exec_driver_sql(create_ddl)
            logger.info(f"  ✅ 已依 source schema 建立 [{table_name}]（{len(col_defs)} 欄）")
            return True
        except Exception as drop_err:
            logger.warning(f"  ⚠️ 無法重建 {table_name}（{drop_err}），改用 TRUNCATE 保留現有 schema")
            try:
                with tgt_engine.begin() as conn:
                    conn.exec_driver_sql(f"TRUNCATE TABLE [{table_name}]")
                logger.info(f"  ✅ 已 TRUNCATE [{table_name}]，將複製數值")
                return True
            except Exception as trunc_err:
                logger.warning(f"  ⚠️ TRUNCATE {table_name} 失敗：{trunc_err}，將由 pandas 自動建立結構")
                return False

    except Exception as e:
        logger.warning(f"  ⚠️ 預建 {table_name} schema 失敗：{e}，將由 pandas 自動建立結構")
        return False


# ---------------------------------------------------------------------------
# DDL fetch & preprocessing
# ---------------------------------------------------------------------------

def fetch_ddl(engine, object_name: str, object_type: str) -> str:
    """
    Views / SPs / Functions：使用 OBJECT_DEFINITION(OBJECT_ID(name))
    Triggers：同上，但加入 parent table 資訊說明
    回傳原始 CREATE 語法字串
    """
    query = f"SELECT OBJECT_DEFINITION(OBJECT_ID('{object_name}'))"
    with engine.connect() as conn:
        result = conn.exec_driver_sql(query).scalar()
        if not result:
            return ""
        if object_type == "TRIGGER":
            parent_q = f"SELECT OBJECT_NAME(parent_id) FROM sys.triggers WHERE object_id = OBJECT_ID('{object_name}')"
            parent = conn.exec_driver_sql(parent_q).scalar()
            return f"-- TRIGGER FOR TABLE: {parent}\n{result}"
        return result


def fetch_dependencies(engine, object_name: str) -> List[Dict[str, str]]:
    """
    查詢 sys.sql_expression_dependencies
    回傳 [{"name": "EMP_DATA", "type": "TABLE"}, ...]
    """
    query = f"""
        SELECT referenced_entity_name, referenced_class_desc
        FROM sys.sql_expression_dependencies
        WHERE referencing_id = OBJECT_ID('{object_name}')
    """
    with engine.connect() as conn:
        deps = []
        for row in conn.exec_driver_sql(query):
            deps.append({"name": row[0], "type": row[1] or "UNKNOWN"})
        return deps


def retarget_ddl(ddl: str, src_db: str, tgt_db: str) -> str:
    """
    將來源 DDL 調整為可在 Target DB 執行的形式。**僅供 clone 流程使用**——
    備份流程絕不可呼叫，否則存下來的會是目標端版本，git 比對基準就失真了。

    1. 用 regex 替換三段式名稱中的來源 DB 名稱為目標 DB 名稱
    2. 確保 CREATE/ALTER VIEW 後的物件名稱有 [] 包裹
    3. 修正常見的 SQL Server 語法相容性問題 (如 float % int)
    """
    if not ddl:
        return ""

    pattern = re.compile(re.escape(f"[{src_db}]"), re.IGNORECASE)
    ddl = pattern.sub(f"[{tgt_db}]", ddl)

    ddl = re.sub(
        r'(?i)((?:CREATE|ALTER)\s+(?:OR\s+ALTER\s+)?VIEW\s+(?:\[?\w+\]?\.)?)(?!\[)([\w\-]+)',
        r'\1[\2]',
        ddl,
        count=1,
    )

    ddl = re.sub(
        r"((?:isNull\(\s*)?sum\([^)]+\)(?:\s*,\s*0\s*\))?)(\s*%\s*\d+)",
        r"CAST(\1 AS INT)\2",
        ddl,
        flags=re.IGNORECASE
    )

    return ddl


# 保留原名供 clone 流程呼叫，行為與拆分前完全一致。
# 待日後要把 clone 也切換成 CREATE OR ALTER 時，
# 在此串上 ddl_backup.normalize_ddl() 並移除各 clone_* 的 DROP 敘述即可。
preprocess_ddl = retarget_ddl


def topological_sort(objects: List[str], engine) -> List[str]:
    """
    利用 sys.sql_expression_dependencies 建立相依圖
    回傳符合建立順序的物件名稱清單
    Cycle 偵測：若發現循環相依，記錄 warning 並跳過排序
    """
    adj      = {obj: [] for obj in objects}
    indegree = {obj: 0  for obj in objects}

    for obj in objects:
        for d in fetch_dependencies(engine, obj):
            dep_name = d["name"]
            if dep_name in objects:
                adj[dep_name].append(obj)
                indegree[obj] += 1

    queue          = [obj for obj in objects if indegree[obj] == 0]
    sorted_objects = []

    while queue:
        curr = queue.pop(0)
        sorted_objects.append(curr)
        for neighbor in adj[curr]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_objects) != len(objects):
        logger.warning("Topological sort detected a cycle. Skipping strict sorting for some objects.")
        for obj in objects:
            if obj not in sorted_objects:
                sorted_objects.append(obj)

    return sorted_objects


# ---------------------------------------------------------------------------
# Clone executors
# ---------------------------------------------------------------------------

def clone_views(selected_views: List[str], src_engine, tgt_engine, src_db: str, tgt_db: str,
                retry_items: list = None) -> None:
    if not selected_views:
        return
    sorted_views = topological_sort(selected_views, src_engine)
    logger.info(f"開始複製 Views ({len(sorted_views)} 個)")
    for view in sorted_views:
        ddl = ""
        try:
            ddl = fetch_ddl(src_engine, view, "VIEW")
            ddl = preprocess_ddl(ddl, src_db, tgt_db)
            safe_view_str = view.replace("'", "''")
            safe_view_id  = view.replace("]", "]]")
            drop_stmt = f"IF OBJECT_ID('{safe_view_str}', 'V') IS NOT NULL DROP VIEW [{safe_view_id}];"
            with tgt_engine.connect() as conn:
                conn.exec_driver_sql(drop_stmt)
                if ddl.strip():
                    conn.exec_driver_sql(ddl)
                conn.commit()
            logger.info(f"✅ View {view} 複製成功")
        except Exception as e:
            logger.error(f"❌ View {view} 複製失敗: {e}")
            if retry_items is not None and ddl.strip():
                safe_str = view.replace("'", "''")
                safe_id  = view.replace("]", "]]")
                retry_items.append({
                    "obj_type": "VIEW",
                    "name": view,
                    "ddl": ddl,
                    "drop_stmt": f"IF OBJECT_ID('{safe_str}', 'V') IS NOT NULL DROP VIEW [{safe_id}];",
                    "error": str(e),
                })


def clone_sps_and_functions(selected: List[str], src_engine, tgt_engine, src_db: str, tgt_db: str,
                            is_func: bool, retry_items: list = None) -> None:
    if not selected:
        return
    sorted_objs  = topological_sort(selected, src_engine)
    obj_type_str = "Function" if is_func else "Stored Procedure"
    logger.info(f"開始複製 {obj_type_str}s ({len(sorted_objs)} 個)")

    for obj in sorted_objs:
        ddl = ""
        try:
            ddl = fetch_ddl(src_engine, obj, "FUNCTION" if is_func else "SP")
            ddl = preprocess_ddl(ddl, src_db, tgt_db)
            drop_type = "FUNCTION" if is_func else "PROCEDURE"
            safe_str  = obj.replace("'", "''")
            safe_id   = obj.replace("]", "]]")
            drop_stmt = (
                f"IF OBJECT_ID('{safe_str}') IS NOT NULL"
                f" AND OBJECTPROPERTY(OBJECT_ID('{safe_str}'), 'IsMSShipped') = 0"
                f" DROP {drop_type} [{safe_id}];"
            )
            with tgt_engine.connect() as conn:
                conn.exec_driver_sql(drop_stmt)
                if ddl.strip():
                    conn.exec_driver_sql(ddl)
                conn.commit()
            logger.info(f"✅ {obj_type_str} {obj} 複製成功")
        except Exception as e:
            logger.error(f"❌ {obj_type_str} {obj} 複製失敗: {e}")
            if retry_items is not None and ddl.strip():
                safe_str  = obj.replace("'", "''")
                safe_id   = obj.replace("]", "]]")
                drop_type = "FUNCTION" if is_func else "PROCEDURE"
                retry_items.append({
                    "obj_type": "FUNCTION" if is_func else "SP",
                    "name": obj,
                    "ddl": ddl,
                    "drop_stmt": f"IF OBJECT_ID('{safe_str}') IS NOT NULL DROP {drop_type} [{safe_id}];",
                    "error": str(e),
                })


def clone_triggers(selected_triggers: List[str], src_engine, tgt_engine, src_db: str, tgt_db: str,
                   retry_items: list = None) -> None:
    if not selected_triggers:
        return
    logger.warning(f"⚠️ 注意：開始複製 Triggers ({len(selected_triggers)} 個)，請確認其對目標 DB 寫入無干擾。")
    for obj in selected_triggers:
        ddl = ""
        try:
            ddl = fetch_ddl(src_engine, obj, "TRIGGER")
            ddl = preprocess_ddl(ddl, src_db, tgt_db)
            safe_str  = obj.replace("'", "''")
            safe_id   = obj.replace("]", "]]")
            drop_stmt = f"IF OBJECT_ID('{safe_str}', 'TR') IS NOT NULL DROP TRIGGER [{safe_id}];"
            with tgt_engine.connect() as conn:
                conn.exec_driver_sql(drop_stmt)
                if ddl.strip():
                    conn.exec_driver_sql(ddl)
                conn.commit()
            logger.info(f"✅ Trigger {obj} 複製成功")
        except Exception as e:
            logger.error(f"❌ Trigger {obj} 複製失敗: {e}")
            if retry_items is not None and ddl.strip():
                safe_str = obj.replace("'", "''")
                safe_id  = obj.replace("]", "]]")
                retry_items.append({
                    "obj_type": "TRIGGER",
                    "name": obj,
                    "ddl": ddl,
                    "drop_stmt": f"IF OBJECT_ID('{safe_str}', 'TR') IS NOT NULL DROP TRIGGER [{safe_id}];",
                    "error": str(e),
                })


def write_retry_script(retry_items: list, src_db: str, tgt_db: str) -> None:
    """
    將 Clone 過程中失敗的 DDL 物件輸出為可重執行的 SQL 腳本。
    使用者補設 Linked Server 等環境後，直接執行此腳本即可。
    """
    if not retry_items:
        return

    linked_servers: set[str] = set()
    for item in retry_items:
        m = re.search(r"Could not find server '([^']+)'", item["error"])
        if m:
            linked_servers.add(m.group(1))

    header_lines = [
        "-- ================================================================",
        f"-- Clone Retry Script  |  產生時間: {_DATE_STR}",
        f"-- Source DB: [{src_db}]",
        f"-- Target DB: [{tgt_db}]",
        "--",
        f"-- 共 {len(retry_items)} 個物件因環境因素無法自動複製。",
    ]
    if linked_servers:
        header_lines.append("-- 執行前請先在 Target SQL Server 設定以下 Linked Server：")
        for ls in sorted(linked_servers):
            header_lines.append(f"--   EXEC sp_addlinkedserver '{ls}', '<provider>', '<datasrc>', ...")
    header_lines += [
        "-- ================================================================",
        "",
        f"USE [{tgt_db.replace(']', ']]')}];",
        "GO",
        "",
    ]

    type_order  = ["VIEW", "FUNCTION", "SP", "TRIGGER"]
    type_labels = {"VIEW": "VIEWS", "FUNCTION": "FUNCTIONS",
                   "SP": "STORED PROCEDURES", "TRIGGER": "TRIGGERS"}
    by_type = {t: [i for i in retry_items if i["obj_type"] == t] for t in type_order}

    body_lines: list[str] = []
    for obj_type in type_order:
        items = by_type[obj_type]
        if not items:
            continue
        label = type_labels[obj_type]
        body_lines.append(f"-- ── {label} ({len(items)}) {'─' * max(0, 54 - len(label))}")
        body_lines.append("")
        for item in items:
            short_err = str(item["error"]).split("\n")[0][:160]
            body_lines.append(f"-- [{item['name']}]")
            body_lines.append(f"-- 失敗原因: {short_err}")
            body_lines.append(item["drop_stmt"])
            body_lines.append("GO")
            if item["ddl"].strip():
                body_lines.append(item["ddl"].strip())
                body_lines.append("GO")
            body_lines.append("")

    with open(RETRY_SCRIPT_FILENAME, "w", encoding="utf-8") as f:
        f.write("\n".join(header_lines + body_lines))

    logger.info(f"📄 {len(retry_items)} 個失敗物件的 DDL 已輸出至 {RETRY_SCRIPT_FILENAME}")


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_db_connection(args=None, project=None):
    """
    Setup source and target database connections.
    Priority: CLI arg > Env var > Project stored config > empty string.
    """
    print("\n--- 設定資料庫連線 ---")

    proj_cfg = {}
    if project is not None:
        proj_cfg = config_mgr.get_connection_config(project.id)

    cli_has_src = args and any(getattr(args, k, None) for k in
                               ["src_server", "src_database", "src_uid", "src_pwd"])
    if project is not None and proj_cfg.get("demo_mode") and not cli_has_src:
        logger.info("🎭 Demo 模式（專案設定）")
        return None, None, "", ""

    def get_conf(arg_name, env_name, proj_key, default_val=""):
        if args and getattr(args, arg_name, None):
            return getattr(args, arg_name)
        env_val = os.environ.get(env_name)
        if env_val:
            return env_val
        return proj_cfg.get(proj_key, default_val)

    src_config = {
        'server':   get_conf('src_server',   'SRC_DB_SERVER', 'src_server'),
        'database': get_conf('src_database', 'SRC_DB_NAME',   'src_database'),
        'uid':      get_conf('src_uid',      'SRC_DB_UID',    'src_uid'),
        'pwd':      get_conf('src_pwd',      'SRC_DB_PWD',    'src_pwd'),
    }
    tgt_config = {
        'server':   get_conf('tgt_server',   'TGT_DB_SERVER', 'tgt_server'),
        'database': get_conf('tgt_database', 'TGT_DB_NAME',   'tgt_database'),
        'uid':      get_conf('tgt_uid',      'TGT_DB_UID',    'tgt_uid'),
        'pwd':      get_conf('tgt_pwd',      'TGT_DB_PWD',    'tgt_pwd'),
    }

    def build_conn_str(cfg):
        encoded_pwd = urllib.parse.quote_plus(cfg['pwd'])
        return f"mssql+pymssql://{cfg['uid']}:{encoded_pwd}@{cfg['server']}/{cfg['database']}"

    logger.info(f"來源 (Source): {src_config['server']} ({src_config['uid']})")
    logger.info(f"目標 (Target): {tgt_config['server']} ({tgt_config['uid']})")

    try:
        src_engine = create_engine(build_conn_str(src_config))
        with src_engine.connect() as conn:
            logger.info("✅ 來源資料庫連線成功！")

        tgt_engine = create_engine(build_conn_str(tgt_config))
        with tgt_engine.connect() as conn:
            logger.info("✅ 目標資料庫連線成功！")

        return src_engine, tgt_engine, src_config['database'], tgt_config['database']

    except Exception as e:
        logger.error(f"❌ 資料庫連線失敗: {e}")
        return None, None, "", ""
