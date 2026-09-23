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
# widen_ansi=True 是建 target 表用的（varchar 放寬 2x 容納 CP950→UTF-8 膨脹），
# False 是讀 target 現況用的；用錯會把放寬後的寬度誤判成不符。
R = ce.render_column_type
eq("decimal 保留 precision/scale", R('decimal', 9, 18, 4, True), 'decimal(18,4)')
eq("numeric 保留 precision/scale", R('numeric', 9, 18, 4, False), 'numeric(18,4)')
eq("varchar 放寬 2x",              R('varchar', 50, 0, 0, True),  'varchar(100)')
eq("varchar 不放寬",               R('varchar', 50, 0, 0, False), 'varchar(50)')
eq("varchar(-1) 為 MAX",           R('varchar', -1, 0, 0, True),  'varchar(MAX)')
eq("varchar 放寬後溢位轉 MAX",     R('varchar', 5000, 0, 0, True),'varchar(MAX)')
eq("nvarchar 位元組數減半",        R('nvarchar', 100, 0, 0, True),'nvarchar(50)')
eq("nvarchar(-1) 為 MAX",          R('nvarchar', -1, 0, 0, True), 'nvarchar(MAX)')
eq("varbinary 不放寬",             R('varbinary', 8000, 0, 0, True), 'varbinary(8000)')
eq("datetime2 取 scale",           R('datetime2', 8, 27, 7, True),'datetime2(7)')
eq("int 無參數",                   R('int', 4, 10, 0, True),      'int')


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

# (name, type_name, max_length, precision, scale, is_nullable, default_def)
SRC = FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None),
    ('SALARY', 'decimal',   9, 18, 4, 1, None),
    ('NAME',   'varchar',  50, 0, 0, 1, None),
])

# 舊版 pandas 推導出來的 target：decimal 變 FLOAT，字串全成 nvarchar(MAX)
legacy = ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', -1, 0, 0, 1, None),
    ('SALARY', 'float',     8, 53, 0, 1, None),
    ('NAME',   'nvarchar', -1, 0, 0, 1, None),
]), 'EMP_DATA')
eq("舊版 target 三欄全部抓到", len(legacy), 3)
eq("decimal→float 被指名",
   [i for i in legacy if i.startswith('[SALARY]')],
   ['[SALARY] 應為 decimal(18,4)，target 實際為 float'])

# 本版建出來的 target 不可有任何誤報：numeric 是 decimal 的同義詞，
# varchar(100) 是 varchar(50) 放寬 2x 的預期結果。
eq("相符時零誤報", ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None),
    ('SALARY', 'numeric',   9, 18, 4, 1, None),
    ('NAME',   'varchar', 100, 0, 0, 1, None),
]), 'EMP_DATA'), [])

eq("欄位增減都回報", sorted(ce.diff_target_schema(SRC, FakeEngine([
    ('EMP_NO', 'nvarchar', 20, 0, 0, 0, None),
    ('SALARY', 'decimal',   9, 18, 4, 1, None),
    ('LEGACY', 'int',       4, 10, 0, 1, None),
]), 'EMP_DATA')), ['[LEGACY] target 多出此欄位（source 沒有）', '[NAME] target 缺少此欄位'])

eq("target 表不存在時不比對", ce.diff_target_schema(SRC, FakeEngine([]), 'EMP_DATA'), [])


# ---- DEFAULT 約束 ----
# 守的是 2026-09 的 bug：建表只帶型別與 NULL，沒帶 DEFAULT。來源 NOT NULL DEFAULT ('')
# 的欄位到 target 沒了預設值，下游省略該欄的 INSERT 就 Msg 515。
eq("無 DEFAULT 不產生子句",   ce.render_default_clause(None), '')
eq("definition 原樣使用",     ce.render_default_clause("('')"), " DEFAULT ('')")

ddl = ce.build_create_table_ddl('SALARY_MONTH', [
    ('bonus_no',     'varchar',   10, 0, 0, 0, "('')"),
    ('amount_self0', 'decimal',    9, 18, 2, 0, '((0))'),
    ('upd_time',     'datetime',   8, 23, 3, 1, '(getdate())'),
    ('emp_no',       'nvarchar',  20, 0, 0, 0, None),
])
eq("字串／數值／函式 DEFAULT 進入 CREATE TABLE，無 DEFAULT 欄不多子句", ddl.splitlines(), [
    'CREATE TABLE [SALARY_MONTH] (',
    "    [bonus_no] varchar(20) NOT NULL DEFAULT (''),",
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
    ('bonus_no', 'varchar', 10, 0, 0, 0, "('')"),
]), tgt, 'SALARY_MONTH'), True)
eq("實際送出的 CREATE TABLE 帶 DEFAULT",
   "[bonus_no] varchar(20) NOT NULL DEFAULT ('')" in tgt.sent[-1], True)

# 沿用 target 既有表（DROP 失敗改 TRUNCATE）時，缺少的 DEFAULT 不會被修正，必須回報
SRC_DF = FakeEngine([
    ('bonus_no',     'varchar', 10, 0, 0, 0, "('')"),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((0))'),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(getdate())'),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None),
])
eq("target 缺 DEFAULT／定義不同都回報，大小寫不同不誤報", ce.diff_target_schema(SRC_DF, FakeEngine([
    ('bonus_no',     'varchar', 20, 0, 0, 0, None),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((1))'),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(GETDATE())'),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None),
]), 'SALARY_MONTH'), [
    "[bonus_no] target 缺少 DEFAULT ('')",
    '[amount_self0] DEFAULT 應為 ((0))，target 實際為 ((1))',
])

mm = []
ce._report_schema_diff(SRC_DF, FakeEngine([
    ('bonus_no',     'varchar', 20, 0, 0, 0, None),
    ('amount_self0', 'decimal',  9, 18, 2, 0, '((0))'),
    ('upd_time',     'datetime', 8, 23, 3, 1, '(getdate())'),
    ('emp_no',       'nvarchar', 20, 0, 0, 0, None),
]), 'SALARY_MONTH', mm)
eq("缺 DEFAULT 進入 mismatches 彙總", mm, ["SALARY_MONTH：[bonus_no] target 缺少 DEFAULT ('')"])


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
