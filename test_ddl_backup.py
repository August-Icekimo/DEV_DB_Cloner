import os, sys, json, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
logging.getLogger("DB_Replicator").addHandler(logging.StreamHandler(sys.stdout))
logging.getLogger("DB_Replicator").setLevel(logging.INFO)

import ddl_backup as db

fails = []
def eq(label, got, want):
    if got != want:
        fails.append(f"{label}\n   got : {got!r}\n   want: {want!r}")

# ---- 動詞改寫 ----
eq("plain CREATE", db.rewrite_leading_verb("CREATE PROCEDURE dbo.a AS SELECT 1")[0],
   "CREATE OR ALTER PROCEDURE dbo.a AS SELECT 1")
eq("plain ALTER", db.rewrite_leading_verb("ALTER PROC dbo.a AS SELECT 1")[0],
   "CREATE OR ALTER PROC dbo.a AS SELECT 1")
eq("already OR ALTER", db.rewrite_leading_verb("CREATE  OR\n  ALTER VIEW v AS SELECT 1")[0],
   "CREATE OR ALTER VIEW v AS SELECT 1")
eq("lowercase", db.rewrite_leading_verb("create function f() returns int as begin return 1 end")[0],
   "CREATE OR ALTER function f() returns int as begin return 1 end")

# 巢狀區塊註解 —— regex 會在第一個 */ 收工而改壞
nested = "/* 修改紀錄 /* 2019 舊版 */ 2024 改版 */\nCREATE PROCEDURE dbo.p AS SELECT 1"
eq("nested comment", db.rewrite_leading_verb(nested)[0],
   "/* 修改紀錄 /* 2019 舊版 */ 2024 改版 */\nCREATE OR ALTER PROCEDURE dbo.p AS SELECT 1")

line_comment = "-- CREATE PROCEDURE 這行是註解\n-- 另一行\nALTER VIEW v AS SELECT 1"
eq("line comment", db.rewrite_leading_verb(line_comment)[0],
   "-- CREATE PROCEDURE 這行是註解\n-- 另一行\nCREATE OR ALTER VIEW v AS SELECT 1")

# body 裡的 CREATE 不可被碰
body = "CREATE PROCEDURE dbo.p AS EXEC('CREATE TABLE #t (a int)')"
eq("body untouched", db.rewrite_leading_verb(body)[0],
   "CREATE OR ALTER PROCEDURE dbo.p AS EXEC('CREATE TABLE #t (a int)')")

# 無法辨識時原樣回傳
eq("unrecognized", db.rewrite_leading_verb("CREATE TABLE t (a int)")[0], "CREATE TABLE t (a int)")
eq("unrecognized kw", db.rewrite_leading_verb("CREATE TABLE t (a int)")[1], None)
eq("garbage", db.rewrite_leading_verb("SELECT 1")[0], "SELECT 1")

# ---- normalize ----
eq("crlf+trailing ws", db.normalize_ddl("CREATE VIEW v AS  \r\nSELECT 1   \r\n\r\n"),
   "CREATE OR ALTER VIEW v AS\nSELECT 1\n")
eq("no verb rewrite", db.normalize_ddl("ALTER VIEW v AS SELECT 1", verb=None),
   "ALTER VIEW v AS SELECT 1\n")

# ---- 檔名 ----
eq("sanitize slash", db.sanitize_filename("dbo.a/b"), "dbo.a_b")
eq("sanitize trailing dot", db.sanitize_filename("dbo.name."), "dbo.name")
eq("reserved", db.sanitize_filename("CON"), "CON_")
eq("cjk kept", db.sanitize_filename("dbo.員工資料"), "dbo.員工資料")

# ---- SET 旗標 ----
eq("qi off preamble",
   db.build_file_content({"definition": "CREATE VIEW v AS SELECT 1", "uses_qi": 0, "uses_an": 1}),
   "SET QUOTED_IDENTIFIER OFF;\nGO\n\nCREATE OR ALTER VIEW v AS SELECT 1\n")
eq("qi on = no preamble",
   db.build_file_content({"definition": "CREATE VIEW v AS SELECT 1", "uses_qi": 1, "uses_an": 1}),
   "CREATE OR ALTER VIEW v AS SELECT 1\n")

# ---- 端對端：落檔、刪除同步、保護跳過物件 ----
class FakeEngine:
    def __init__(self, rows): self.rows = rows
    def connect(self): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def exec_driver_sql(self, q):
        class R:
            def __init__(s, rows): s._r = rows
            def fetchall(s): return s._r
        return R(self.rows)

tmp = tempfile.mkdtemp()
root = os.path.join(tmp, "sql_backup")
bdir = os.path.join(root, "HRM")

# git repo 不存在 → 應中止
os.makedirs(bdir)
rc = db.run_backup(FakeEngine([]), "HRM", "srv", "hrm", backup_root=root)
eq("abort when not a git repo", rc, 1)

subprocess.run(["git", "init", "-q"], cwd=bdir, check=True)
subprocess.run(["git", "config", "user.email", "t@t"], cwd=bdir, check=True)
subprocess.run(["git", "config", "user.name", "t"], cwd=bdir, check=True)

# (schema, name, type, definition, uses_qi, uses_an)
rows1 = [
    ("dbo", "V_Emp",   "V",  "CREATE VIEW V_Emp AS SELECT 1", 1, 1),
    ("dbo", "usp_Foo", "P",  "ALTER PROCEDURE usp_Foo AS SELECT 2", 1, 1),
    ("dbo", "fn_Bar",  "FN", "CREATE FUNCTION fn_Bar() RETURNS int AS BEGIN RETURN 1 END", 1, 1),
    ("dbo", "usp_Enc", "P",  None, None, None),   # 加密物件
]
rc = db.run_backup(FakeEngine(rows1), "HRM", "srv", "hrm", backup_root=root)
eq("run1 rc", rc, 0)
eq("view written", os.path.exists(os.path.join(bdir, "Views", "dbo.V_Emp.sql")), True)
with open(os.path.join(bdir, "StoredProcedures", "dbo.usp_Foo.sql")) as f:
    eq("ALTER normalized on disk", f.read(), "CREATE OR ALTER PROCEDURE usp_Foo AS SELECT 2\n")
eq("scaffold readme", os.path.exists(os.path.join(bdir, "README.md")), True)
eq("scaffold gitattributes", os.path.exists(os.path.join(bdir, ".gitattributes")), True)
man = json.load(open(os.path.join(bdir, MANIFEST := db.MANIFEST_FILENAME)))
eq("manifest skipped", [s["name"] for s in man["skipped"]], ["usp_Enc"])
eq("manifest counts added", man["counts"]["added"], 3)

# 手動放一個「加密物件先前可讀」的檔案，之後必須被保護不刪
enc_path = os.path.join(bdir, "StoredProcedures", "dbo.usp_Enc.sql")
open(enc_path, "w").write("CREATE OR ALTER PROCEDURE usp_Enc AS SELECT 9\n")

subprocess.run(["git", "add", "-A"], cwd=bdir, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=bdir, check=True)

# 第二次：fn_Bar 從來源消失、V_Emp 內容改變、usp_Enc 仍加密
rows2 = [
    ("dbo", "V_Emp",   "V", "CREATE VIEW V_Emp AS SELECT 99", 1, 1),
    ("dbo", "usp_Foo", "P", "ALTER PROCEDURE usp_Foo AS SELECT 2", 1, 1),
    ("dbo", "usp_Enc", "P", None, None, None),
]
rc = db.run_backup(FakeEngine(rows2), "HRM", "srv", "hrm", backup_root=root)
eq("run2 rc", rc, 0)
eq("orphan deleted", os.path.exists(os.path.join(bdir, "Functions", "dbo.fn_Bar.sql")), False)
eq("encrypted file protected", os.path.exists(enc_path), True)
man = json.load(open(os.path.join(bdir, db.MANIFEST_FILENAME)))
eq("run2 counts", (man["counts"]["updated"], man["counts"]["unchanged"], man["counts"]["deleted"]),
   (1, 1, 1))

# dirty working tree → 中止
rc = db.run_backup(FakeEngine(rows2), "HRM", "srv", "hrm", backup_root=root)
eq("abort when dirty", rc, 1)
rc = db.run_backup(FakeEngine(rows2), "HRM", "srv", "hrm", backup_root=root, allow_dirty=True)
eq("allow-dirty proceeds", rc, 0)

# 外層 repo 的 rev-parse 不可誤判為內層 repo
outer = tempfile.mkdtemp()
subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
sub = os.path.join(outer, "sql_backup", "P1")
os.makedirs(sub)
ok, why = db.check_git_repo(sub)
eq("nested dir not mistaken for repo", ok, False)

print()
if fails:
    print(f"❌ {len(fails)} FAILED")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("✅ all assertions passed")
