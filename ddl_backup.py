"""
ddl_backup.py — 將 Source DB 的 programmable objects 匯出為一物件一檔的 SQL 快照。

用途是讓備份目錄成為一個獨立的 git repo，以 git diff 追蹤 DDL 變更歷史。
涵蓋 VIEW / PROCEDURE / FUNCTION / TRIGGER，不含 TABLE。

設計要點：
  * 抓取為單一 query（sys.sql_modules），要嘛全成功要嘛全失敗，中止即無害
  * 落檔順序為「先寫入更新、最後刪除孤兒」，崩潰時留下多餘檔而非缺檔
  * 檔案內容不含任何會變動的資訊（時間戳等一律進 _manifest.json）
  * 工具對 git 唯讀：只檢查 repo 是否存在、working tree 是否乾淨
"""
import os
import re
import json
import hashlib
import logging
import subprocess
import urllib.parse
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from sqlalchemy import create_engine

logger = logging.getLogger("DB_Replicator")

MANIFEST_FILENAME = "_manifest.json"
DEFAULT_BACKUP_ROOT = "sql_backup"

# sys.objects.type → 輸出子目錄
TYPE_DIRS = {
    "V":  "Views",
    "P":  "StoredProcedures",
    "FN": "Functions",
    "IF": "Functions",
    "TF": "Functions",
    "TR": "Triggers",
    # CLR 物件沒有 T-SQL 定義，仍列入查詢以便回報為 skipped
    "PC": "StoredProcedures",
    "FS": "Functions",
    "FT": "Functions",
    "TA": "Triggers",
}

CLR_TYPES = {"PC", "FS", "FT", "TA"}

MANAGED_DIRS = ("Views", "StoredProcedures", "Functions", "Triggers")


# ---------------------------------------------------------------------------
# T-SQL 前導雜訊掃描
#
# sys.sql_modules.definition 回傳的是最後一次 CREATE 或 ALTER 的原始文字，
# 開頭動詞會因物件歷史而不同。要穩定地改寫它，必須先跳過前導的空白與註解。
# T-SQL 的區塊註解可以巢狀（/* a /* b */ c */），regex 無法正確處理，
# 因此這裡用逐字元掃描。
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_OBJECT_KEYWORDS = {"PROCEDURE", "PROC", "FUNCTION", "VIEW", "TRIGGER"}


def _skip_trivia(text: str, i: int = 0) -> int:
    """回傳 text 中自 i 起第一個非空白、非註解字元的索引。"""
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n\f\v":
            i += 1
        elif text.startswith("--", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif text.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth > 0:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            break
    return i


def _read_word(text: str, i: int) -> Tuple[Optional[str], int]:
    m = _WORD_RE.match(text, i)
    return (m.group(0), m.end()) if m else (None, i)


def rewrite_leading_verb(ddl: str, verb: str = "CREATE OR ALTER") -> Tuple[str, Optional[str]]:
    """
    將定義開頭的 CREATE / ALTER / CREATE OR ALTER 改寫為指定動詞。

    回傳 (改寫後的 DDL, 物件關鍵字)。若開頭不是可辨識的建立敘述則原樣回傳
    且關鍵字為 None——寧可不動，也不要改壞。
    """
    start = _skip_trivia(ddl)
    word, after = _read_word(ddl, start)
    if not word or word.upper() not in ("CREATE", "ALTER"):
        return ddl, None

    end = after
    if word.upper() == "CREATE":
        # 消化既有的 "OR ALTER"
        i = _skip_trivia(ddl, after)
        w2, j2 = _read_word(ddl, i)
        if w2 and w2.upper() == "OR":
            i2 = _skip_trivia(ddl, j2)
            w3, j3 = _read_word(ddl, i2)
            if w3 and w3.upper() == "ALTER":
                end = j3

    # 確認後面接的確實是物件類型關鍵字，避免誤改到別的東西
    i = _skip_trivia(ddl, end)
    kw, _ = _read_word(ddl, i)
    if not kw or kw.upper() not in _OBJECT_KEYWORDS:
        return ddl, None

    return ddl[:start] + verb + ddl[end:], kw.upper()


def normalize_ddl(ddl: str, verb: Optional[str] = "CREATE OR ALTER") -> str:
    """
    正規化 DDL 文字以利 git diff：
      * 換行統一為 LF（SQL Server 存的是 CRLF）
      * 去除每行尾端空白
      * 檔尾統一一個換行
      * 開頭動詞改寫為 verb（傳 None 則不改寫）
    不做任何語法重排或美化，那會讓 diff 失真。
    """
    if not ddl:
        return ""

    text = ddl.replace("\r\n", "\n").replace("\r", "\n")
    if verb:
        text, _ = rewrite_leading_verb(text, verb)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 檔名處理
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """把物件名轉為各平台皆安全的檔名主體（不含副檔名）。"""
    safe = _UNSAFE_CHARS.sub("_", name)
    safe = safe.rstrip(" .")           # Windows 不允許結尾的空白或句點
    if not safe:
        safe = "_"
    if safe.upper() in _WINDOWS_RESERVED:
        safe = f"{safe}_"
    return safe


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

# 單一 query 撈完全部定義：抓取階段因此具備原子性，
# 失敗時一個檔案都還沒被動過。
_FETCH_QUERY = """
    SELECT
        s.name                    AS schema_name,
        o.name                    AS object_name,
        o.type                    AS object_type,
        m.definition              AS definition,
        m.uses_quoted_identifier  AS uses_qi,
        m.uses_ansi_nulls         AS uses_an
    FROM sys.objects o
    JOIN sys.schemas s        ON s.schema_id  = o.schema_id
    LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
    LEFT JOIN sys.triggers   tr ON tr.object_id = o.object_id
    WHERE o.is_ms_shipped = 0
      AND o.type IN ('V','P','FN','IF','TF','TR','PC','FS','FT','TA')
      AND (o.type NOT IN ('TR','TA') OR tr.parent_class = 1)
    ORDER BY s.name, o.name
"""


def fetch_all_modules(engine) -> List[Dict]:
    """一次撈回全部 programmable objects 的定義與 SET 旗標。"""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(_FETCH_QUERY).fetchall()

    modules = []
    for schema_name, object_name, object_type, definition, uses_qi, uses_an in rows:
        modules.append({
            "schema":     schema_name,
            "name":       object_name,
            "type":       (object_type or "").strip(),
            "definition": definition,
            "uses_qi":    uses_qi,
            "uses_an":    uses_an,
        })
    return modules


def build_file_content(module: Dict) -> str:
    """
    組出單一物件的檔案內容。

    QUOTED_IDENTIFIER / ANSI_NULLS 不存在定義文字裡，而是 sys.sql_modules 的
    旗標欄位。只在偏離預設（ON）時補上 SET，讓絕大多數檔案保持乾淨。
    """
    preamble = []
    if module.get("uses_qi") == 0:
        preamble.append("SET QUOTED_IDENTIFIER OFF;")
    if module.get("uses_an") == 0:
        preamble.append("SET ANSI_NULLS OFF;")

    body = normalize_ddl(module["definition"])
    if not preamble:
        return body
    return "\n".join(preamble) + "\nGO\n\n" + body


# ---------------------------------------------------------------------------
# git 唯讀檢查
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: str) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd,
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except FileNotFoundError:
        return 127, "git 指令不存在"
    except subprocess.TimeoutExpired:
        return 124, "git 指令逾時"


def check_git_repo(backup_dir: str) -> Tuple[bool, str]:
    """
    確認 backup_dir 本身就是一個 git repo 的根目錄。

    只檢查 is-inside-work-tree 是不夠的——備份目錄若尚未 git init，
    該指令會沿著目錄樹往上找到外層的工具 repo 而回報成功。
    因此必須比對 toplevel 是否等於 backup_dir 本身。
    """
    if not os.path.isdir(backup_dir):
        return False, "目錄不存在"

    code, out = _git(["rev-parse", "--show-toplevel"], backup_dir)
    if code != 0:
        return False, "不是 git repo"

    if os.path.realpath(out) != os.path.realpath(backup_dir):
        return False, f"本身不是 git repo 根目錄（目前隸屬於 {out}）"

    return True, ""


def check_git_clean(backup_dir: str) -> Tuple[bool, List[str]]:
    """working tree 是否乾淨。回傳 (是否乾淨, 未提交項目清單)。"""
    code, out = _git(["status", "--porcelain"], backup_dir)
    if code != 0:
        return False, ["無法讀取 git status"]
    entries = [ln for ln in out.split("\n") if ln.strip()]
    return (not entries), entries


# ---------------------------------------------------------------------------
# 附帶產生的檔案（皆僅在不存在時建立，之後交給使用者維護）
# ---------------------------------------------------------------------------

_INNER_GITIGNORE = """# 每次備份都會變動的執行資訊，不進版控
_manifest.json
"""

_INNER_GITATTRIBUTES = """# SQL Server 的定義存的是 CRLF，統一為 LF 以免跨平台產生整檔 diff
*.sql text eol=lf
"""

_HOWTO_README = """# README 產生指示（給 AI Agent）

本檔為 DEV_DB_Cloner 備份功能產生的佔位檔。
請閱讀本目錄後，將**整份檔案覆寫**為正式的 repo 說明。

## 你的任務

掃描本目錄結構與 `_manifest.json`，撰寫一份給人閱讀的 README，須涵蓋：

1. 這個 repo 是什麼 — 某 SQL Server 資料庫 programmable objects 的定期快照，
   用途是追蹤 DDL 變更歷史
2. 目錄結構說明（Views / Functions / StoredProcedures / Triggers）
3. 檔案內容的性質 — 來自 `sys.sql_modules`，開頭動詞已正規化為
   `CREATE OR ALTER`，**非來源逐字複本**
4. 明確不涵蓋的項目 — 物件權限、extended properties、synonym、
   使用者自訂型別、sequence、以及所有 table 結構。
   這不是災難復原用的備份
5. 日常用法 — 備份後如何 git diff / commit 檢視變更

## 硬性限制

- **不得寫入任何會變動的資訊**：時間戳、物件數量、伺服器名稱、最後備份日期。
  這些都在 `_manifest.json`（已 gitignore），寫進 README 會導致每次備份
  都產生無意義的 diff
- 本目錄的 .sql 檔可能含有內部主機名、IP 與商業邏輯，
  撰寫過程請勿將檔案內容傳送至外部服務
"""


def _write_if_absent(path: str, content: str) -> bool:
    if os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _plan(modules: List[Dict]) -> Tuple[List[Dict], List[Dict], Dict[str, str]]:
    """
    Phase 2：記憶體內處理。將物件分成「可寫入」與「跳過」兩類，
    並算出每個物件的目標路徑（含檔名碰撞處理）。
    """
    writable, skipped = [], []
    used: Dict[str, str] = {}   # 小寫相對路徑 → 來源物件全名（碰撞偵測）
    planned: Dict[str, str] = {}

    for m in sorted(modules, key=lambda x: (x["schema"].lower(), x["name"].lower())):
        full_name = f"{m['schema']}.{m['name']}"
        subdir = TYPE_DIRS.get(m["type"])

        if subdir is None:
            skipped.append({**m, "reason": f"未支援的物件類型 {m['type']}"})
            continue

        if not m["definition"]:
            reason = "CLR 物件，無 T-SQL 定義" if m["type"] in CLR_TYPES \
                     else "定義無法讀取（可能為 WITH ENCRYPTION）"
            skipped.append({**m, "reason": reason})
            continue

        base = sanitize_filename(full_name)
        rel = os.path.join(subdir, f"{base}.sql")

        # 大小寫不敏感的碰撞偵測：git 在 Linux 上區分大小寫，
        # 但 Windows / macOS 的檔案系統不區分，兩邊都要能正確運作。
        key = rel.lower()
        if key in used and used[key] != full_name:
            rel = os.path.join(subdir, f"{base}~{_short_hash(full_name)}.sql")
            key = rel.lower()
            logger.warning(f"  ⚠️ 檔名碰撞：{full_name} 改用 {rel}")
        used[key] = full_name

        planned[full_name] = rel
        writable.append({**m, "rel_path": rel, "full_name": full_name})

    return writable, skipped, planned


def _existing_sql_files(backup_dir: str) -> List[str]:
    """列出備份目錄中由本工具管理的 .sql 檔（相對路徑）。"""
    found = []
    for subdir in MANAGED_DIRS:
        d = os.path.join(backup_dir, subdir)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith(".sql"):
                found.append(os.path.join(subdir, fn))
    return found


def run_backup(engine, project_name: str, src_server: str, src_database: str,
               backup_root: str = DEFAULT_BACKUP_ROOT,
               dry_run: bool = False, allow_dirty: bool = False) -> int:
    """
    執行一次完整備份。回傳 0 表示成功，非 0 表示中止。

    四階段：前置檢查 → 全量抓取 → 記憶體內處理 → 落檔，
    任一階段中止都不會留下缺檔。
    """
    backup_dir = os.path.join(backup_root, sanitize_filename(project_name))

    logger.info("=" * 60)
    logger.info(f"DDL 備份  |  專案: {project_name}")
    logger.info(f"來源: {src_server} / {src_database}")
    logger.info(f"輸出: {backup_dir}")
    if dry_run:
        logger.info("模式: DRY-RUN（不會寫入任何檔案）")
    logger.info("=" * 60)

    # --- Phase 0：前置檢查（此階段不碰任何檔案）---
    ok, why = check_git_repo(backup_dir)
    if not ok:
        logger.error(f"❌ 備份目錄尚未初始化為 git repo（{why}）")
        logger.error("   本工具對 git 唯讀，請自行執行：")
        logger.error(f"     mkdir -p {backup_dir} && git -C {backup_dir} init")
        return 1

    if not dry_run:
        clean, entries = check_git_clean(backup_dir)
        if not clean and not allow_dirty:
            logger.error("❌ 備份目錄有未提交的變更，中止以免覆寫掉尚未進版控的內容：")
            for e in entries[:20]:
                logger.error(f"     {e}")
            if len(entries) > 20:
                logger.error(f"     ...（共 {len(entries)} 項）")
            logger.error("   請先 git commit，或加上 --allow-dirty 強制執行。")
            return 1

    # --- Phase 1：全量抓取（單一 query，原子性）---
    logger.info("正在讀取物件定義...")
    try:
        modules = fetch_all_modules(engine)
    except Exception as e:
        logger.error(f"❌ 讀取物件定義失敗，未變更任何檔案：{e}")
        return 1
    logger.info(f"   共取得 {len(modules)} 個物件")

    # --- Phase 2：記憶體內處理 ---
    writable, skipped, _ = _plan(modules)

    expected = {m["rel_path"] for m in writable}
    existing = set(_existing_sql_files(backup_dir))

    # 加密 / CLR 物件的既有檔案要保留，不能當成孤兒刪掉——
    # 否則 git 上會顯示成「物件被刪除」，那是誤導。
    protected = set()
    for s in skipped:
        subdir = TYPE_DIRS.get(s["type"])
        if subdir:
            base = sanitize_filename("{}.{}".format(s["schema"], s["name"]))
            protected.add(os.path.join(subdir, base + ".sql"))

    orphans = sorted(existing - expected - protected)

    added = updated = unchanged = 0
    failed: List[Dict] = []

    # --- Phase 3：落檔。先寫入更新，最後才刪除孤兒 ---
    # 崩潰時留下的是多餘的舊檔（git status 看得到、下次備份會清掉），
    # 而不是無聲消失的檔案。
    for m in writable:
        abs_path = os.path.join(backup_dir, m["rel_path"])
        content = build_file_content(m)

        prev = None
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", newline="") as f:
                    prev = f.read()
            except Exception:
                prev = None

        if prev == content:
            unchanged += 1
            continue

        if dry_run:
            if prev is None:
                added += 1
                logger.info(f"  + {m['rel_path']}")
            else:
                updated += 1
                logger.info(f"  M {m['rel_path']}")
            continue

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            if prev is None:
                added += 1
                logger.info(f"  + {m['rel_path']}")
            else:
                updated += 1
                logger.info(f"  M {m['rel_path']}")
        except Exception as e:
            # 單一物件寫檔失敗不中止全局，記錄後繼續
            logger.error(f"  ❌ 寫入 {m['rel_path']} 失敗：{e}")
            failed.append({"name": m["full_name"], "error": str(e)})

    deleted = 0
    for rel in orphans:
        if dry_run:
            logger.info(f"  - {rel}")
            deleted += 1
            continue
        try:
            os.remove(os.path.join(backup_dir, rel))
            logger.info(f"  - {rel}")
            deleted += 1
        except Exception as e:
            logger.error(f"  ❌ 刪除 {rel} 失敗：{e}")
            failed.append({"name": rel, "error": str(e)})

    # --- 附帶檔案與 manifest ---
    if not dry_run:
        for fn, content in (
            (".gitignore",     _INNER_GITIGNORE),
            (".gitattributes", _INNER_GITATTRIBUTES),
            ("README.md",      _HOWTO_README),
        ):
            if _write_if_absent(os.path.join(backup_dir, fn), content):
                logger.info(f"  + {fn}")

        manifest = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "project":      project_name,
            "server":       src_server,
            "database":     src_database,
            "counts": {
                "total":     len(modules),
                "added":     added,
                "updated":   updated,
                "unchanged": unchanged,
                "deleted":   deleted,
                "skipped":   len(skipped),
                "failed":    len(failed),
            },
            "objects": [
                {"schema": m["schema"], "name": m["name"],
                 "type": m["type"], "file": m["rel_path"]}
                for m in writable
            ],
            "skipped": [
                {"schema": s["schema"], "name": s["name"],
                 "type": s["type"], "reason": s["reason"]}
                for s in skipped
            ],
            "failed": failed,
        }
        with open(os.path.join(backup_dir, MANIFEST_FILENAME), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    # --- Phase 4：報表 ---
    logger.info("-" * 60)
    logger.info(f"新增 {added} / 更新 {updated} / 未變動 {unchanged} / "
                f"刪除 {deleted} / 跳過 {len(skipped)} / 失敗 {len(failed)}")

    if skipped:
        logger.info("跳過的物件（既未寫入也未刪除既有檔案）：")
        for s in skipped:
            logger.info(f"  · {s['schema']}.{s['name']} [{s['type']}] — {s['reason']}")

    if failed:
        logger.warning("以下項目處理失敗：")
        for f_ in failed:
            logger.warning(f"  · {f_['name']} — {f_['error']}")

    if dry_run:
        logger.info("DRY-RUN 結束，未寫入任何檔案。")
    else:
        logger.info(f"完成。請至 {backup_dir} 檢視 git diff 後提交。")

    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# 來源連線（備份只需要 Source，不建立 Target 連線）
# ---------------------------------------------------------------------------

def resolve_source_config(args=None) -> Dict[str, str]:
    """優先序：CLI 參數 > 環境變數。"""
    def pick(arg_name: str, env_name: str) -> str:
        if args and getattr(args, arg_name, None):
            return getattr(args, arg_name)
        return os.environ.get(env_name, "")

    return {
        "server":   pick("src_server",   "SRC_DB_SERVER"),
        "database": pick("src_database", "SRC_DB_NAME"),
        "uid":      pick("src_uid",      "SRC_DB_UID"),
        "pwd":      pick("src_pwd",      "SRC_DB_PWD"),
    }


def create_source_engine(cfg: Dict[str, str]):
    encoded_pwd = urllib.parse.quote_plus(cfg["pwd"])
    url = f"mssql+pymssql://{cfg['uid']}:{encoded_pwd}@{cfg['server']}/{cfg['database']}"
    engine = create_engine(url)
    with engine.connect():
        pass
    return engine
