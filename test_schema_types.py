"""
欄位型別複製的回歸測試。

守的是 v1.3.0 之前的 bug：當時完全不預建 target 表，交給 pandas 依 DataFrame
dtype 推導建表，而 read_sql 的 coerce_float 預設會把 decimal.Decimal 轉成
float64 —— 來源的 decimal(18,4) 到了 target 就變成 FLOAT。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
logging.getLogger("DB_Replicator").addHandler(logging.StreamHandler(sys.stdout))
logging.getLogger("DB_Replicator").setLevel(logging.CRITICAL)

from decimal import Decimal
import pandas as pd

import clone_engine as ce
import db_replicator as dr

fails = []
def eq(label, got, want):
    if got != want:
        fails.append(f"{label}\n   got : {got!r}\n   want: {want!r}")


# ---- 型別字串組裝 ----
# varchar/char 長度照抄來源，不再放寬 2x（2026-10 起讀取端改在來源 CAST 成 nvarchar、
# 欄位沿用來源 collation，位元組數不會膨脹；放寬反而讓 char 多補空白）。
R = ce.render_column_type
eq("decimal 保留 precision/scale", R('decimal', 9, 18, 4), 'decimal(18,4)')
eq("numeric 保留 precision/scale", R('numeric', 9, 18, 4), 'numeric(18,4)')
eq("varchar 長度照抄",             R('varchar', 50, 0, 0),  'varchar(50)')
eq("char 長度照抄，不多補空白",    R('char', 10, 0, 0),     'char(10)')
eq("varchar(8000) 不轉 MAX",       R('varchar', 8000, 0, 0),'varchar(8000)')
eq("varchar(-1) 為 MAX",           R('varchar', -1, 0, 0),  'varchar(MAX)')
eq("nvarchar 位元組數減半",        R('nvarchar', 100, 0, 0),'nvarchar(50)')
eq("nvarchar(-1) 為 MAX",          R('nvarchar', -1, 0, 0), 'nvarchar(MAX)')
eq("varbinary 不放寬",             R('varbinary', 8000, 0, 0), 'varbinary(8000)')
eq("datetime2 取 scale",           R('datetime2', 8, 27, 7),'datetime2(7)')
eq("int 無參數",                   R('int', 4, 10, 0),      'int')


# ---- target 結構比對 ----
class _FakeConn:
    def __init__(self, rows): self.rows = rows
    def exec_driver_sql(self, q): return self.rows
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeEngine:
    """只回固定 sys.columns 結果的假 engine。"""
    def __init__(self, rows): self.rows = rows
    def connect(self): return _FakeConn(self.rows)

# (name, type_name, max_length, precision, scale, is_nullable, default_def, collation)
SRC = FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None, None),
    ('SALARY', 'decimal',   9, 18, 4, 1, None, None),
    ('NAME',   'varchar',  50, 0, 0, 1, None, None),
])

# 舊版 pandas 推導出來的 target：decimal 變 FLOAT，字串全成 nvarchar(MAX)
legacy = ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', -1, 0, 0, 1, None, None),
    ('SALARY', 'float',     8, 53, 0, 1, None, None),
    ('NAME',   'nvarchar', -1, 0, 0, 1, None, None),
]), 'EMP_DATA')
eq("舊版 target 三欄全部抓到", len(legacy), 3)
eq("decimal→float 被指名",
   [i for i in legacy if i.startswith('[SALARY]')],
   ['[SALARY] 應為 decimal(18,4)，target 實際為 float'])

# 本版建出來的 target 不可有任何誤報：numeric 是 decimal 的同義詞。
eq("相符時零誤報", ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None, None),
    ('SALARY', 'numeric',   9, 18, 4, 1, None, None),
    ('NAME',   'varchar',  50, 0, 0, 1, None, None),
]), 'EMP_DATA'), [])

eq("舊版放寬 2x 建出的 varchar 會被回報", ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None, None),
    ('SALARY', 'decimal',   9, 18, 4, 1, None, None),
    ('NAME',   'varchar', 100, 0, 0, 1, None, None),
]), 'EMP_DATA'), ['[NAME] 應為 varchar(50)，target 實際為 varchar(100)'])

eq("欄位增減都回報", sorted(ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None, None),
    ('SALARY', 'decimal',   9, 18, 4, 1, None, None),
    ('LEGACY', 'int',       4, 10, 0, 1, None, None),
]), 'EMP_DATA')), ['[LEGACY] target 多出此欄位（source 沒有）', '[NAME] target 缺少此欄位'])

eq("target 表不存在時不比對", ce.diff_target_schema(SRC, FakeEngine([]), 'EMP_DATA'), [])


# ---- DEFAULT 約束 ----
# 守的是 2026-09 的 bug：建表只帶型別與 NULL，沒帶 DEFAULT。來源 NOT NULL DEFAULT ('')
# 的欄位到 target 沒了預設值，下游省略該欄的 INSERT 就 Msg 515。
eq("無 DEFAULT 不產生子句",   ce.render_default_clause(None), '')
eq("definition 原樣使用",     ce.render_default_clause("('')"), " DEFAULT ('')")

ddl = ce.build_create_table_ddl('SALARY_MONTH', [
    ('bonus_no',     'varchar',   10, 0, 0, 0, "('')", None),
    ('amount_self0', 'decimal',    9, 18, 2, 0, '((0))', None),
    ('upd_time',     'datetime',   8, 23, 3, 1, '(getdate())', None),
    ('emp_no',       'nvarchar',  20, 0, 0, 0, None, None),
])
eq("字串／數值／函式 DEFAULT 進入 CREATE TABLE，無 DEFAULT 欄不多子句", ddl.splitlines(), [
    'CREATE TABLE [SALARY_MONTH] (',
    "    [bonus_no] varchar(10) NOT NULL DEFAULT (''),",
    '    [amount_self0] decimal(18,2) NOT NULL DEFAULT ((0)),',
    '    [upd_time] datetime NULL DEFAULT (getdate()),',
    '    [emp_no] nvarchar(10) NOT NULL',
    ')',
])
eq("約束交給 SQL Server 自動命名", 'CONSTRAINT' in ddl, False)


class _RecordingConn(_FakeConn):
    def __init__(self, sink): self.sink = sink
    def exec_driver_sql(self, q): self.sink.append(q)

class RecordingEngine:
    """記下所有送到 target 的 SQL。"""
    def __init__(self): self.sent = []
    def begin(self): return _RecordingConn(self.sent)

tgt = RecordingEngine()
eq("建表成功", ce.create_target_table_from_source(FakeEngine([
    ('bonus_no', 'varchar', 10, 0, 0, 0, "('')", None),
]), tgt, 'SALARY_MONTH'), True)
eq("實際送出的 CREATE TABLE 帶 DEFAULT",
   "[bonus_no] varchar(10) NOT NULL DEFAULT ('')" in tgt.sent[-1], True)

# 沿用 target 既有表（DROP 失敗改 TRUNCATE）時，缺少的 DEFAULT 不會被修正，必須回報
SRC_DF = FakeEngine([
    ('bonus_no',     'varchar', 10, 0, 0, 0, "('')", None),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((0))', None),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(getdate())', None),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None, None),
])
eq("target 缺 DEFAULT／定義不同都回報，大小寫不同不誤報", ce.diff_target_schema(SRC_DF, FakeEngine([
    ('bonus_no',     'varchar', 10, 0, 0, 0, None, None),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((1))', None),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(GETDATE())', None),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None, None),
]), 'SALARY_MONTH'), [
    "[bonus_no] target 缺少 DEFAULT ('')",
    '[amount_self0] DEFAULT 應為 ((0))，target 實際為 ((1))',
])

mm = []
ce._report_schema_diff(SRC_DF, FakeEngine([
    ('bonus_no',     'varchar', 10, 0, 0, 0, None, None),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((0))', None),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(getdate())', None),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None, None),
]), 'SALARY_MONTH', mm)
eq("缺 DEFAULT 進入 mismatches 彙總", mm, ["SALARY_MONTH：[bonus_no] target 缺少 DEFAULT ('')"])


# ---- COLLATE ----
# 守的是 2026-10 的 bug：建表沒帶 collation，target 套用容器預設 SQL_Latin1_General_CP1_CI_AS，
# 來源中文 collation 的 varchar 到了 target 變 ??? 或 Big5 位元組被當 Latin1 顯示。
TW = 'Chinese_Taiwan_Stroke_CI_AS'
TW_BIN = 'Chinese_Taiwan_Stroke_BIN'
C = ce.render_collate_clause
eq("varchar 帶 COLLATE",          C('varchar', TW), f' COLLATE {TW}')
eq("nvarchar／char／text 也帶",   [C(t, TW) for t in ('nvarchar', 'CHAR', 'text', 'ntext')], [f' COLLATE {TW}'] * 4)
eq("非字串型別不帶",              C('int', TW), '')
eq("沒有 collation 不帶",         C('varchar', None), '')
try:
    C('varchar', "x; DROP TABLE t--")
    fails.append("不合法的 collation 名稱應該 raise")
except ValueError:
    pass

ddl = ce.build_create_table_ddl('SYS_COM_DTL', [
    ('main_code', 'varchar',  10, 0, 0, 0, None,   TW),
    ('code_val1', 'nvarchar', 100, 0, 0, 1, "('')", 'Chinese_Taiwan_Stroke_BIN'),
    ('seq',       'int',       4, 10, 0, 0, '((0))', None),
])
eq("COLLATE 接在型別後、NULL 與 DEFAULT 之前；逐欄沿用各自的 collation", ddl.splitlines(), [
    'CREATE TABLE [SYS_COM_DTL] (',
    f'    [main_code] varchar(10) COLLATE {TW} NOT NULL,',
    "    [code_val1] nvarchar(50) COLLATE Chinese_Taiwan_Stroke_BIN NULL DEFAULT (''),",
    '    [seq] int NOT NULL DEFAULT ((0))',
    ')',
])

SRC_CO = FakeEngine([
    ('main_code', 'varchar', 10, 0, 0, 0, None, TW),
    ('seq',       'int',      4, 10, 0, 0, None, None),
])
eq("target 欄位 collation 不同要回報，大小寫不同不誤報", ce.diff_target_schema(SRC_CO, FakeEngine([
    ('main_code', 'varchar', 10, 0, 0, 0, None, 'SQL_Latin1_General_CP1_CI_AS'),
    ('seq',       'int',      4, 10, 0, 0, None, None),
]), 'SYS_COM_DTL'), [f'[main_code] COLLATE 應為 {TW}，target 實際為 SQL_Latin1_General_CP1_CI_AS'])
eq("collation 相符零誤報", ce.diff_target_schema(SRC_CO, FakeEngine([
    ('main_code', 'varchar', 10, 0, 0, 0, None, TW.lower()),
    ('seq',       'int',      4, 10, 0, 0, None, None),
]), 'SYS_COM_DTL'), [])


# ---- 讀取端：ANSI 字串在 source 端 CAST 成 nvarchar ----
# 守的是 2026-10 實測確認的 bug：pymssql 內建 FreeTDS 不看欄位 collation，varchar 一律
# Latin1 解碼，cp950「陳婉玲」讀成 '³¯°û¬Â'；target 改中文 collation 後會被轉成亂碼。
sel = ce.build_select_list([
    ('id',  'int',       4, 10, 0, 0, None, None),
    ('v',   'varchar',  20, 0, 0, 1, None, TW_BIN),
    ('ch',  'CHAR',     10, 0, 0, 1, None, TW_BIN),
    ('vm',  'varchar',  -1, 0, 0, 1, None, TW_BIN),
    ('big', 'varchar', 8000, 0, 0, 1, None, TW_BIN),
    ('t',   'text',     16, 0, 0, 1, None, TW_BIN),
    ('nv',  'nvarchar', 40, 0, 0, 1, None, TW_BIN),
    ('a]b', 'varchar',   5, 0, 0, 1, None, TW_BIN),
])
eq("char/varchar/text 轉 nvarchar、長度超過 4000 或 text 用 MAX、其餘原樣、別名維持原名", sel.split(', '), [
    '[id]',
    'CAST([v] AS nvarchar(20)) AS [v]',
    'CAST([ch] AS nvarchar(10)) AS [ch]',
    'CAST([vm] AS nvarchar(MAX)) AS [vm]',
    'CAST([big] AS nvarchar(MAX)) AS [big]',
    'CAST([t] AS nvarchar(MAX)) AS [t]',
    '[nv]',
    'CAST([a]]b] AS nvarchar(5)) AS [a]]b]',
])
eq("沒有欄位資訊退回 *", ce.build_select_list([]), '*')
eq("搬資料的 SELECT 必須走 build_select_list",
   'build_select_list(' in __import__('inspect').getsource(dr._execute_replication), True)


# ---- 姓名字庫（DB 模式）同樣要在 source 端 CAST ----
import data_anonymizer as da
eq("姓名欄位 CAST 成 nvarchar、識別字以 [..] 包起", da.build_name_query('EMP_DATA', 'emp_name'),
   "SELECT DISTINCT CAST([emp_name] AS nvarchar(MAX)) AS [emp_name] FROM [EMP_DATA] "
   "WHERE [emp_name] IS NOT NULL AND LEN([emp_name]) >= 2")
eq("識別字裡的 ] 會跳脫", da.build_name_query('a]b', 'c]d'),
   "SELECT DISTINCT CAST([c]]d] AS nvarchar(MAX)) AS [c]]d] FROM [a]]b] "
   "WHERE [c]]d] IS NOT NULL AND LEN([c]]d]) >= 2")


# ---- 資料庫層級 collation 預檢 ----
class _CollResult:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row

class CollEngine:
    """只回 (DB collation, server collation) 的假 engine。"""
    def __init__(self, db, server): self.row = (db, server)
    def connect(self): return _FakeConn(_CollResult(self.row))

LATIN = 'SQL_Latin1_General_CP1_CI_AS'
eq("DB 相符就不中止（UAT 實況：server CI_AS、DB BIN）",
   ce.check_collations(CollEngine(TW_BIN, TW), CollEngine(TW_BIN, TW)), [])
eq("只有 server 不同：只警告不中止", ce.check_collations(CollEngine(TW, TW), CollEngine(TW, LATIN)), [])
issues = ce.check_collations(CollEngine(TW, TW), CollEngine(LATIN, LATIN))
eq("DB collation 不同：中止並提示建庫語法",
   any(f'COLLATE {TW}' in i and 'CREATE DATABASE' in i for i in issues), True)

# 不一致時 _execute_replication 必須在動到任何資料表之前中止；加旗標才繼續
touched = []
_orig_create, _orig_check = dr.create_target_table_from_source, dr.check_collations
dr.create_target_table_from_source = lambda *a, **k: touched.append(a[2]) or False
dr.check_collations = lambda s, t: ['mismatch']
try:
    dr._execute_replication({'tables': ['EMP_DATA']}, object(), object(), 'src', 'tgt')
    eq("collation 不一致時不碰任何資料表", touched, [])
    try:
        dr._execute_replication({'tables': ['EMP_DATA']}, object(), object(), 'src', 'tgt',
                                allow_collation_mismatch=True)
    except Exception:
        pass  # 假 engine 撐不到搬資料，只看有沒有走到建表
    eq("--allow-collation-mismatch 會繼續複製", touched, ['EMP_DATA'])
finally:
    dr.create_target_table_from_source, dr.check_collations = _orig_create, _orig_check


# ---- NVARCHAR 宣告只挑真字串 ----
# coerce_float=False 之後 decimal 會以 Decimal 物件留在 object dtype，
# 若沿用「object dtype 一律 NVARCHAR」會把數值欄位宣告成文字。
chunk = pd.DataFrame({
    'NAME':   ['王小明', '李小華'],
    'SALARY': [Decimal('1.50'), Decimal('2.25')],
    'PHOTO':  [b'\x01\x02', b'\x03'],
    'AGE':    [30, 40],
    'RATIO':  [1.5, 2.5],
    'EMPTY':  [None, None],
})
eq("只有字串欄與全空欄轉 NVARCHAR", sorted(dr._text_dtype_map(chunk)), ['EMPTY', 'NAME'])


# ---- read_sql 不得把 Decimal 轉成 float ----
import inspect
eq("呼叫端必須顯式關掉 coerce_float",
   'coerce_float=False' in inspect.getsource(dr._execute_replication), True)

# 若 pandas 哪天改掉預設值，這行會提醒我們上面那個顯式參數已可拿掉
eq("pandas coerce_float 預設仍為 True",
   inspect.signature(pd.read_sql).parameters['coerce_float'].default, True)


if fails:
    print("\n".join(f"❌ {f}" for f in fails))
    sys.exit(1)
print("✅ all assertions passed")
