# 修正：clone 出來的資料表沒有 IDENTITY — 2026-10-21（未發佈）

**fix: keep IDENTITY(seed, increment) on cloned tables; copy source values with IDENTITY_INSERT**

> ⚠️ **在這個修正之前 clone 的資料庫都沒有任何 IDENTITY 欄位**（`sys.identity_columns` 為 0），省略 IDENTITY 欄的 INSERT 會 Msg 515。要重新 clone，或在來源端執行 `gen_identity_sequences.sql`，把產出的 SEQUENCE + DEFAULT 套到 target。
> 已知受影響：`hrm_0907`（來源 UAT 172.22.1.130/hrm_test）。`SYS_TRANS_LOG.seq_no` 已手動補上 `dbo.SEQ_SYS_TRANS_LOG`，產生器會略過它。

- **問題**：`create_target_table_from_source` 刻意不帶 IDENTITY，好讓來源值能直接 INSERT。複製本身不會出錯，但之後省略該欄、靠自動編號的 INSERT 全部失敗。實例：HRM 的 MSDB／SysDbLog 寫 `SYS_TRANS_LOG` 時不給 `seq_no`（來源是 `numeric(18,0) IDENTITY`），`hrm_0907` 自 clone 後所有交易 log 都沒寫進去，而且錯誤沒有浮出來。
- **建表**：欄位查詢 LEFT JOIN `sys.identity_columns`，`seed_value`／`increment_value` 在 source 端轉成字串，避免 pymssql 讀 sql_variant 出問題。建表時寫出 `IDENTITY(seed,increment)`，接在型別後面（`[seq_no] numeric(18,0) IDENTITY(1,1) NOT NULL`）。
- **搬資料**：表上有 IDENTITY 時，`SET IDENTITY_INSERT [t] ON` 與 pandas `to_sql` 必須用**同一條連線**。這個設定是 session 層級的，把 engine 交給 pandas 會從 pool 另拿一條連線，INSERT 就會 Msg 544。設定不受 COMMIT 影響，所以照舊每個 chunk commit 一次，不會把整張表包成一個大交易。結束時（含失敗）一定會 `OFF`，成功後再 `DBCC CHECKIDENT (..., RESEED)`，讓之後的自動編號從已複製資料的最大值往後接。套用篩選條件只複製部分資料時，也是從複製進來的最大值往後接。
- **沿用既有表時會回報**：DROP 失敗改走 TRUNCATE 時，是否開 IDENTITY_INSERT 依 **target 實際結構**判斷（舊表沒有 IDENTITY，硬開會失敗），資料照常寫入。結構比對會把「target 缺少 IDENTITY」、「seed／increment 不同」、「target 多出 IDENTITY」寫進 ERROR log 與結束時的彙總。
- **既有測試庫的補救：`gen_identity_sequences.sql`**：既有欄位無法事後 ALTER 成 IDENTITY，所以改用 SEQUENCE + `DEFAULT (NEXT VALUE FOR ...)` 做到「省略欄位時自動編號」。HRM 程式沒有用 `SCOPE_IDENTITY`／`@@IDENTITY`／`IDENT_CURRENT`，這樣就夠了。用法、可重複執行的規則與 `gen_default_constraints.sql` 相同：在來源執行，輸出套到 target。每個來源 IDENTITY 欄位會建 `<schema>.SEQ_<table>`，START 在 target 執行當下以 `ISNULL(MAX(col), seed - increment) + increment` 計算（increment 為負時用 MIN），再加上 `DF_<table>_<col>`。target 該欄已有 DEFAULT 或已是 IDENTITY 就略過。
- PK、索引、CHECK、FK 仍不複製。

---

# 修正：clone 出來的資料庫與欄位 collation 變成容器預設值 — 2026-10-20（未發佈）

**fix: preserve source collation on cloned tables; abort on database collation mismatch**

> ⚠️ **在這個修正之前 clone 的資料表，字串欄位都是 target 資料庫的預設 collation**（容器上是 `SQL_Latin1_General_CP1_CI_AS`）。要以來源 collation 重建資料庫後重新 clone。
> 已知受影響：`hrm_0805`、`hrm_0907`（來源 UAT 172.22.1.130/hrm_test）。

- **問題**：整個工具都沒處理 collation，target 資料庫與欄位都套用了容器 SQL Server 的預設值。後果是 SP 裡沒加 `N` 前綴的中文常值變成 `???`，varchar 中文也無法正確落地或顯示。
- **欄位**：欄位查詢加帶 `sys.columns.collation_name`，建表時字串欄位（char/varchar/text/nchar/nvarchar/ntext）寫出 `COLLATE <來源欄位 collation>`，接在型別後面（`[main_code] varchar(20) COLLATE Chinese_Taiwan_Stroke_CI_AS NOT NULL`）。逐欄沿用來源的值，所以跟來源資料庫預設不同的欄位也會保留。
- **資料庫**：本工具不建立 target 資料庫。開始複製前會比對 source／target 的 `DATABASEPROPERTYEX(DB_NAME(),'Collation')`，不一致就中止，並提示 `CREATE DATABASE ... COLLATE ...`。可加 `--allow-collation-mismatch` 強制繼續。target 伺服器預設 collation（也就是 tempdb 的）與 source 不同時只發出警告。
- **沿用既有表時會回報**：DROP 失敗改走 TRUNCATE 時，欄位 collation 不同會跟型別、DEFAULT 不符一樣寫進 ERROR log 與結束時的彙總。
- **讀取端（實測確認的根因）**：pymssql 2.3.13 內建的 FreeTDS v1.4.27 不看欄位 collation，varchar 一律以 Latin1 解碼——cp950 的「陳婉玲」(`0xB3AFB0FBACC2`) 讀成 `'³¯°û¬Â'`（TDS 7.4；改 `tds_version` 7.0／7.3 結果相同；`charset='CP950'` 能讀對 varchar，卻會讓 nvarchar 讀成空字串）。舊版 target 欄位是 Latin1，以 `N''` 寫回剛好還原同樣位元組，所以 `hrm_0907` 的位元組是對的、顯示是錯的；但 target 改成中文 collation 後會被轉成亂碼（`0x33A1C2A258753F41`），**只改 COLLATE 會讓資料更糟**。修正：搬資料的 SELECT 把 char／varchar／text 在 source 端 `CAST(... AS nvarchar(n|MAX))`，交給 SQL Server 依來源 collation 轉成 Unicode，寫入時以 `N'...'` 送出、由 target 欄位 collation 轉回 code page。
- **實測（本機容器，假資料）**：source `Chinese_Taiwan_Stroke_BIN`，varchar／char／text／varchar(max)（含「許功蓋」等 0x5C 尾碼字）、Latin1 collation 欄位（café）、nvarchar、decimal。新版 clone 到 BIN 與 Latin1 預設的 target，位元組與 Unicode 讀值全部與 source 一致；模擬舊版（不帶 COLLATE、`SELECT *`）完全重現 `hrm_0907` 的現象。
- **姓名字庫（DB 模式）同樣修正**：`init_names_from_database` 原本直接 `SELECT DISTINCT {column}`，來源欄位是 varchar 時讀到 Latin1 亂碼。3 字姓名讀成 6 個字元，被長度判斷丟棄；2 字姓名讀成 4 個字元，被當複姓切成亂碼，字庫因此壞掉。預設的 `EMP_DATA.emp_name` 是 nvarchar，不受影響。改成在 source 端 `CAST(... AS nvarchar(MAX))`，識別字以 `[..]` 包起並跳脫 `]`。容器實測（varchar 假資料）：「陳婉玲」切成「陳」+「婉玲」，「歐陽小華」切成「歐陽」+「小華」。盤點其餘讀取 source 的地方（TUI 連線測試、物件清單、`OBJECT_DEFINITION`、`sys.sql_modules.definition`、相依性查詢），都只讀 metadata 或 nvarchar，不需要改。
- **取消 varchar/char 長度 2x 放寬**：v1.3.0 為了 CP950→UTF-8 膨脹把 varchar/char 長度乘 2，但膨脹其實是讀取端 Latin1 誤讀造成的。讀取端修好、collation 也一致之後，位元組數不會改變，長度改為照抄來源。放寬會把 char(10) 建成 char(20) 並多補空白，`DATALENGTH` 與來源不同。沿用舊表（TRUNCATE 路徑）時，舊版放寬過的欄位會列入結構不符彙總。
- **連帶影響**：varchar 欄位的去敏化函式以前收到的是 Latin1 亂碼，現在會收到正確中文，產出結果可能與舊版不同。

---

# 修正：clone 出來的資料表缺少 DEFAULT 約束 — 2026-09-23（未發佈）

**fix: carry DEFAULT constraints when pre-creating target tables**

> ⚠️ **在這個修正之前 clone 的資料庫都缺少 DEFAULT 約束**，需要重新 clone，或在來源端產生 `ALTER TABLE ... ADD DEFAULT ...` 手動補回 target。
> 已知受影響：2026-09-18 clone 的 `hrm_0907`（來源 UAT 172.22.1.130/hrm_test）。

- **問題**：`create_target_table_from_source` 依來源 `sys.columns` 建 target 表時只帶型別與 `NULL`/`NOT NULL`，沒帶 DEFAULT。複製本身不會出錯（每一欄都有值），但之後省略該欄、靠預設值補值的 INSERT 會失敗（Msg 515 Cannot insert the value NULL）。實例：下游 `sp_SRB1000` 寫入 `SALARY_MONTH` 未給 `bonus_no`（來源 `NOT NULL DEFAULT ('')`），寫入 `INS_PAYRECORD` 未給 `amount_self0/1`、`amount_cmp0/1`（來源 `DEFAULT ((0))`）。當次 clone 完的 target 全庫只剩 4 個 DEFAULT，卻有 1,284 個 `NOT NULL` 且無預設值的欄位。
- **修正**：欄位查詢 LEFT JOIN `sys.default_constraints`，把 `definition` 原樣接在欄位定義後面（`[bonus_no] varchar(20) NOT NULL DEFAULT ('')`）。約束名稱交給 SQL Server 自動命名，不沿用來源名稱，避免撞到 target 上殘留物件的同名約束導致整張表建立失敗。
- **沿用既有表時會回報**：DROP 失敗改走 TRUNCATE 時不會重建資料表，缺少的 DEFAULT 不會被補上。此時結構比對會把「source 有 DEFAULT、target 沒有（或定義不同）」寫成 ERROR log，並列入 clone 結束時的結構不符彙總，與型別不符的處理方式相同。
- IDENTITY、PK、索引、CHECK、FK 仍不複製（IDENTITY 為刻意不帶；2026-10-21 起改為照帶）。

---

# 版本發佈 v1.3.0 (Pre) — 2026-05-22

## 模組拆分、Clone 穩定性修正與失敗 DDL Retry Script
**refactor + fix: module split, CP950 truncation fix, DDL syntax fixes, retry script output**

---

### 1. 模組架構拆分 (Module Split)

將原本 2,649 行的 `db_replicator.py` 拆分為三個職責清晰的獨立模組，大幅降低日後維護與 context 負擔：

| 新檔案 | 行數 | 職責 |
|:---|:---:|:---|
| `clone_engine.py` | 470 | 所有 DB fetch、DDL 前處理、clone 執行、retry script、連線建立 |
| `tui_screens.py` | 1,314 | 所有 Textual TUI 畫面類別 |
| `db_replicator.py` | 471 | 薄 orchestration layer（原 2,649 行）|

- `config_manager.py` 新增 module-level `config_mgr` singleton，各模組直接 import，避免循環依賴。
- Import graph 嚴格保持無環：`db_replicator → clone_engine / tui_screens / config_manager / data_anonymizer`；`tui_screens → clone_engine / config_manager`。

### 2. varchar 截斷修正 (CP950 → UTF-8 2x 膨脹)

- **問題**：pymssql 以 CP950 bigram bytes 讀取後，UTF-8 最大膨脹率可達 2x（罕見字、補充字集 U+20000+ 及 Latin-1 誤讀情境均為 2x，原 1.5x 不足）。複製 `SALARY_TXN.txn_reason` 等欄位時會觸發 SQL Server 錯誤 2628（String or binary data would be truncated）。
- **修正**：`create_target_table_from_source` 建立目標資料表時，`varchar`/`char` 欄位長度一律乘以 **2**（上限 8000 者自動轉為 `MAX`）。

### 3. View 名稱含連字號語法修正

- **問題**：`OBJECT_DEFINITION()` 回傳的 DDL 中，含 `-` 的 View 名稱未加中括號，導致 T-SQL 解析為減法運算，觸發錯誤 102（Incorrect syntax）。
- **修正**：`preprocess_ddl()` 新增 regex，對 `CREATE/ALTER VIEW` 後的物件名稱補上 `[bracket]`；`DROP VIEW` / `DROP PROCEDURE` / `DROP TRIGGER` 的動態語句同步修正為 `[{safe_id}]` 格式。

### 4. 失敗 DDL Retry Script 輸出

- **功能**：每次 clone 結束後，若有 View / SP / Function / Trigger 因環境因素（如 Linked Server 未設定）失敗，自動將其 DDL 輸出至 `YYYYMMDD_Clone_Retry.sql`。
- **格式**：每段 DDL 前附帶冪等 `DROP` 語句，可在目標環境設定完成後直接重複執行。
- **適用情境**：開發環境缺少正式環境 Linked Server 時，先完成資料表複製，事後再補建 DDL 物件。

---

### 5. DDL Deploy Profile (設定檔匯出) ← 2026-04-28

- **標準化 Schema**：實作 JSON v1.0 規範，完整封裝資料表、檢視表、預存程序等物件選取，以及對應的篩選條件與 PII 規則。
- **TUI 匯出工作流**：在物件選擇畫面新增 `X` 快捷鍵，支援差異偵測、路徑自定義、Trigger 安全警告。

### 6. 無人值守部署 (Headless Mode) ← 2026-04-28

- **CLI 整合**：新增 `--deploy-profile <PATH>` 參數，可跳過 TUI 直接進入複製流程。
- **嚴謹驗證**：Headless 模式下強制執行 PII 函數白名單檢查與連線參數完整性驗證。
- **靈活連線**：支援從 CLI 參數或環境變數 (`SRC_DB_PWD` 等) 讀取敏感連線資訊。
- **姓名資料自動關聯**：自動從 `config.db` 找尋同名專案以套用對應的姓名來源設定。

### 7. ConfigManager 核心重構 ← 2026-04-28

- **狀態隔離**：修正 `save_project_state` 邏輯，確保儲存 Table 設定時不會誤觸其他物件的選取狀態。
- **白名單驗證**：新增 `VALID_ANON_FUNCTIONS` 常數，強化去敏化流程的安全性。

### 升級須知

> `config.db` 結構無需任何修改。若使用 PyInstaller 打包，需確認 `clone_engine.py` 與 `tui_screens.py` 已納入 spec 檔的 `datas` 或 `hiddenimports`。

### 無人值守模式 (Headless Mode)
```bash
python db_replicator.py --deploy-profile my_project_profile.json \
  --src-pwd "source_password" --tgt-pwd "target_password"

# 或使用環境變數
export SRC_DB_PWD="your_password"
python db_replicator.py --deploy-profile my_project_profile.json
```

---

# 版本發佈 v1.2.0 (Release v1.2.0) — 2026-03-20

## 完成了一系列 TUI 介面優化、功能增強以及主題化工程
**feat: TUI interface optimization, feature enhancement and theming**

---

### 1. 核心 Bug 修正
- 修正 `ProjectSelector` 誤植與嵌套方法的問題，將 `TableItem` 正確分離為獨立元件，解決啟動崩潰。

### 2. 專案選擇器 (Project Selector) 強化
- **功能擴充**：新增「複製專案」按鈕，可深層複製所有資料表選取、篩選條件及 PII 規則。
- **介面優化**：
    - 按鈕採用中英對照並顯示快捷鍵提示 (N/C/O/D)。
    - 實作完整鍵盤快捷鍵綁定。
    - 專案清單高度固定為 7 行並支援捲軸。
    - 實作光棒選取效果，啟動時自動聚焦第一項。

### 3. 設定畫面 (Project Settings) 改造
- **標籤式切換**：姓名來源改用類似分頁標籤的選單，並支援藍白色系動態切換效果。
- **輸入優化**：更換為單行 `Input` 元件，優化輸入體驗。
- **按鈕與快捷鍵**：重新設計灰底 (Cancel) 與綠底 (Save) 按鈕，並支援 C 與 S 快捷鍵。

### 4. 流程與導覽增強
- **返回功能**：在 Table Selector 畫面新增 `Ctrl+O` 快捷鍵，可直接返回專案選擇清單。
- **主迴圈重構**：調整 `run_replication` 執行流程，支援無縫切換專案。

### 5. 主題化與標準化
- **顏色系統**：全面移除硬編碼顏色代碼，改用 Textual 官方 **Design Tokens** (如 `$primary`, `$success`)，確保 App 能完美適配深色/淺色主題。
- **文件規範**：新增 `schema.md`，依照統一格式完整定義 `config.db` 的欄位結構與 ER 關係。

### 舊資料庫升級指令 (Upgrade Database)
若您從舊版升級至 v1.2.0，請手動更新 `config.db` 結構以支援新欄位：
```bash
sqlite3 config.db "ALTER TABLE project_tables ADD COLUMN object_type VARCHAR DEFAULT 'TABLE'; UPDATE project_tables SET object_type = 'TABLE' WHERE object_type IS NULL; SELECT id, table_name, object_type FROM project_tables LIMIT 10;"
```

---

# 版本發佈 v1.1.0 (Release v1.1.0) — 2026-02-11

## 零依賴靜態編譯、專案管理系統、UI 互動全面升級
**feat: zero-dependency static build, SQLite project management, UI/UX overhaul**

`feat: replace pyodbc with pymssql, add SQLite config manager, project import/export, interactive UI enhancements`

---

### 🔧 核心架構變更 (Core Architecture)

1. **pyodbc → pymssql 遷移**
   - 將資料庫連接從 `mssql+pyodbc` 改為 `mssql+pymssql`（TDS 協議直連）
   - **完全移除 ODBC 驅動依賴**，實現真正的零依賴靜態編譯
   - 更新 `requirements.txt`、`environment.yml`、`PACKAGING.md`

2. **SQLite 專案設定管理 (ConfigManager)**
   - 所有專案設定（篩選條件、PII 規則、選取狀態）統一儲存於 `config.db`
   - 支援多專案管理（新建/複製/刪除/切換）
   - Legacy JSON 設定檔自動遷移至 SQLite

### ✨ 新功能 (New Features)

3. **專案匯入/匯出 (Import/Export)**
   - **匯出 (E)**: 將專案設定匯出為 `{專案名}_filters.json` + `{專案名}_sensitive_columns.json`
   - **匯入 (I)**: 指定檔案名稱前綴，從 JSON 匯入設定到選定專案
   - 新增 `ConfigManager.export_to_json()` / `import_from_json()` 方法

4. **說明頁面 (Info Screen)**
   - 新增 `Info.txt` 版本說明文件
   - 按 `?` 開啟捲動式說明頁面，含版本資訊、快捷鍵列表、更新紀錄
   - 按 `Q` 或 `ESC` 關閉

5. **離開功能 (Exit)**
   - 專案選擇畫面按 `X` 可直接離開程式

### 🖱️ UI/UX 互動增強 (UI/UX Enhancements)

6. **滑鼠雙擊支援**
   - 專案列表雙擊即開啟專案
   - 資料表選擇畫面左上角專案名稱可點擊返回專案選擇

7. **Hover 互動效果**
   - 專案名稱 Badge：hover 時背景變亮 + 加底線
   - Column Header (TABLES/DATA_FILTERS/PII)：hover 時背景高亮 + 底線
   - ListView 項目：hover 時顯示淡色背景
   - Info-hints 標籤：hover 時文字變成警告色

8. **專案選擇畫面第二列按鈕**
   - 新增 匯入(I) / 匯出(E) / 離開(X) / 說明(?) 四個功能按鈕

### 🐛 Bug 修復 (Bug Fixes)

9. **確認畫面迴圈問題**
   - 修復 `run_replication` 的 `while True` 迴圈缺少 `break`
   - 按 Y 確認後不再回到專案選擇，正確進入複製流程

### 📦 建置與打包 (Build & Packaging)

10. **PyInstaller 靜態編譯修復**
    - 新增 `--hidden-import=pymssql` / `sqlalchemy.dialects.mssql.pymssql`
    - 新增 `--collect-all pymssql` / `rich` / `textual` 確保動態載入模組完整打包
    - 修復 Windows PowerShell 換行問題（`shell: bash`）
    - 修復 Python 3.9 相容性（`str | None` → `Optional[str]`）
    - 移除 `unixodbc-dev` 系統依賴安裝步驟

### 環境需求 (Prerequisites)
- Python 3.9+
- pymssql >= 2.2（透過 TDS 協議直連 SQL Server，無需 ODBC 驅動）
- 執行檔模式下無需安裝任何額外套件

---

# 首次提交 / 版本發佈 v1.0.0 (First Commit / Release v1.0.0)

## 第一次寫標題，不知道寫什麼好 (Title Unknown)
**功能：具備智慧去識別化功能的 HRM 資料庫複製器首次發佈**
`feat: initial release of HRM DB Replicator with smart anonymization`

## 說明 (Description)
此版本推出了 `DEV_DB_Cloner` 工具，這是一個基於 Python 的強大工具，用於複製 SQL Server 資料庫並整合了 PII（個人識別資訊）保護機制。
`This release introduces the DEV_DB_Cloner tool, a robust Python-based utility for replicating SQL Server databases with integrated PII protection.`

### 核心功能 (Key Capabilities):

1. **互動式選單 (Interactive Selection)**:
   * **中文**：提供基於文字的使用者介面 (TUI)，方便快速選擇欲複製的資料表。
   * **English**: Textual-based TUI for easy table selection.

2. **進階脫敏引擎 (Advanced Anonymization Engine)**:
   * **`obfuscate_name`**: 具備語境感知能力的中文姓名生成，支持 2/3/4 字格式。
     `Context-aware Chinese name generation based on character counts (2/3/4 chars).`
   * **`obfuscate_spouse_name`**: 透過加鹽 (Salted) 邏輯，確保配偶姓名與員工本人不同。
     `Salted logic to ensure spouse names differ from employee names.`
   * **`obfuscate_address`**: 支援全形數字正規化及生成寫實的地址結構。
     `Full-width digit normalization and realistic address structure generation.`
   * **`obfuscate_phone`**: 在分析並遮罩末 5 碼的同時，保持嚴格的格式要求。
     `Preserves strict formatting while analyzing and masking the last 5 digits.`
   * **`clear_content`**: 安全抹除高度敏感欄位（例如英文姓名）。
     `Securely scrubs highly sensitive fields (e.g., English Names).`

3. **稽核與可追溯性 (Audit & Traceability)**:
   * **檔案記錄 (File Logging)**：生成每日日誌 (`YYYYMMDD_Clone.log`)，詳列執行步驟與資料範例。
     `Generates daily log files (YYYYMMDD_Clone.log) with detailed execution steps and data samples.`
   * **基於日期的鹽值 (Date-based Salt)**：將執行日期注入脫敏種子，確保同日內結果一致，不同日間具備變異性。
     `Injects execution date into anonymization seeds to ensure consistent results within the same day while varying across different days.`

4. **資料完整性 (Data Integrity)**:
   * 對所有字串欄位強制執行 `NVARCHAR` 映射，以解決 SQL Server 上的中文字碼問題。
     `Enforces NVARCHAR mapping for all string columns to resolve Chinese encoding issues on SQL Server.`
   * 自動從來源 `EMP_DATA` 初始化姓名語料庫，以生成更真實的測試數據。
     `Auto-initializes name corpus from source EMP_DATA for realistic test data generation.`

5. **效能表現 (Performance)**:
   * 採用 `tqdm` 進行批次處理與進度可視化。
     `Batch processing with tqdm progress visualization.`

### 配置說明 (Configuration)
* 針對 `EMP_DATA`、`ADVANCE_BONUS_GRANT` 及 `DEPENDENT_DATA` 預設了「敏感欄位對照表」(`SENSITIVE_COLUMNS`)。
  `Pre-configured SENSITIVE_COLUMNS map for EMP_DATA, ADVANCE_BONUS_GRANT, and DEPENDENT_DATA.`
* 內建大型資料表（如：日誌、出勤紀錄）過濾功能，以優化同步時間。
  `Built-in filtering for large tables (e.g., Logs, Attendance) to optimize sync time.`
* 沒有`EMP_DATA的資料表，請自行尋找自己專案中類似的資料表，並修改`SENSITIVE_COLUMNS`的值。

### 環境需求 (Prerequisites)
* Python 3.x
* ODBC Driver 18 for SQL Server