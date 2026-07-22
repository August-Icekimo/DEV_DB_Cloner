# DEV_DB_Cloner — 測試資料庫複製工具

一個 Python 專案，用於將來源 SQL Server 資料庫複製到目標開發環境，並由內建的去識別化引擎自動處理敏感個資 (PII)。

## 主要功能 (Key Features)

- **互動式 TUI 介面**: 使用 Textual 框架提供終端機圖形介面，方便選擇要複製的資料表。
- **多專案管理**: 透過 SQLite 管理多組專案設定，支援匯入/匯出 JSON。
- **自動去識別化 (Smart Anonymization)**:
  - **姓名混淆**: 基於真實姓名統計資料庫或來源資料庫動態產生的字庫，並支援配偶姓名區隔邏輯。
  - **地址處理**: 支援全形轉半形、縣市識別，並隨機產生行政區與門牌號碼。
  - **身分證/電話**: 自動遮罩身分證字號與手機號碼 (保留末碼格式)。
  - **內容清除**: 針對高敏感欄位 (如英文姓名) 提供直接清空功能。
- **稽核與追蹤 (Audit & Traceability)**:
  - **日誌記錄 (Logging)**: 自動產生 `YYYYMMDD_Clone.log`，詳實記錄執行過程與 Before/After 樣本。
  - **動態 Salt**: 依據執行日期動態產生混淆種子，確保當日結果一致，不同日結果不同。
- **Unicode 支援**: 自動偵測 CP950→UTF-8 最大膨脹率（2x）並放寬欄位長度，確保中文資料正確寫入，不再發生截斷錯誤。
- **高效傳輸**: 支援分批次 (Batch) 讀取與寫入，並顯示進度條。
- **無人值守部署 (Headless Mode)**: 支援讀取 Deploy Profile JSON，透過 CLI 實現自動化批次執行，無需人工干預 TUI。
- **Retry Script**: 複製結束後自動輸出 `YYYYMMDD_Clone_Retry.sql`，將因 Linked Server 等環境因素失敗的 DDL 整理為可重執行腳本，方便事後補建。

---

## 模組架構 (Architecture)

| 檔案 | 職責 |
|:---|:---|
| `db_replicator.py` | 薄 orchestration layer：日誌設定、`apply_anonymization`、`_execute_replication`、`run_replication`、CLI 入口 |
| `clone_engine.py` | 所有 DB 操作：fetch 物件清單、建立目標資料表、DDL 前處理、clone 執行、Retry Script 輸出、連線建立 |
| `tui_screens.py` | 所有 Textual TUI 畫面類別（`ProjectSelector`、`TableSelector` 及所有 ModalScreen）|
| `config_manager.py` | SQLAlchemy ORM 模型、`ConfigManager` 類別、`config_mgr` singleton |
| `data_anonymizer.py` | 所有 PII 去識別化函數 |
| `ddl_backup.py` | DDL 快照備份：物件定義批次抓取、T-SQL 前導雜訊掃描、檔案同步、git 唯讀檢查 |

---

## 安裝方式 (Installation)

### 方式一：下載執行檔（推薦一般使用者）

直接從 [GitHub Releases](../../releases) 下載對應平台的執行檔，無需安裝 Python 或任何套件。

| 平台 | 檔案名稱 |
|:---|:---|
| Windows | `DB_Cloner_Windows.exe` |
| macOS | `DB_Cloner_macOS` |
| Linux | `DB_Cloner_Linux` |

#### 使用步驟

1. 下載執行檔到工作目錄
2. 直接執行：
   ```bash
   # Windows
   .\DB_Cloner_Windows.exe

   # Linux / macOS
   chmod +x DB_Cloner_Linux   # 首次需賦予執行權限
   ./DB_Cloner_Linux
   ```

3. 透過參數指定連線資訊（進入 TUI前）：
   ```bash
   ./DB_Cloner_Linux \
     --src-server 172.22.1.34 --src-database hrm --src-uid sa --src-pwd "password" \
     --tgt-server localhost --tgt-database hrm_dev --tgt-uid sa --tgt-pwd "password"
   ```

#### 重要：備份 `config.db`

- 程式會在執行目錄下自動產生 `config.db`（SQLite），儲存你的所有專案設定。
- **升級版本或搬移目錄時，請一併攜帶 `config.db`**，否則專案設定會遺失。
- **(v1.3.0 升級須知)** 本版本將 `db_replicator.py` 拆分為三個獨立模組（`clone_engine.py`、`tui_screens.py`、薄 orchestration `db_replicator.py`）。若使用 PyInstaller 打包，需確保三個新模組均包含在 spec 檔案中；功能與設定完全相容，`config.db` 無需任何升級動作。
- **(v1.2.0 升級須知)** 若您從舊版升級，請手動執行以下指令將資料庫結構升級：
  ```bash
  sqlite3 config.db "ALTER TABLE project_tables ADD COLUMN object_type VARCHAR DEFAULT 'TABLE'; UPDATE project_tables SET object_type = 'TABLE' WHERE object_type IS NULL; SELECT id, table_name, object_type FROM project_tables LIMIT 10;"
  ```
- 你也可以透過 `匯出 (E)` 功能將設定備份為 JSON 檔案，之後再用 `匯入 (I)` 還原。

---

### 方式二：Python 開發環境

適用於需要修改程式碼或在開發環境中執行的使用者。

#### 系統需求
- Python 3.9+
- SQL Server（來源與目標）

#### 選項 A：使用 Conda
```bash
conda env create -f environment.yml
conda activate dev_db_cloner
# 若更新 environment.yml 後需同步環境：
# conda env update --file environment.yml --prune
```

#### 選項 B：使用 pip / venv
```bash
python -m venv .clone.venv
source .clone.venv/bin/activate  # Linux/Mac
# .clone.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

#### 執行
```bash
python db_replicator.py
```

#### 注意事項
- v1.1.0 起已改用 `pymssql`（TDS 協議直連），**不再需要安裝 ODBC Driver**。
- 如使用舊版程式碼（v1.0.0），仍需安裝 ODBC Driver 18。
- `config.db` 與 `OBFUSCATE_NAME.json` 為執行時自動產生的檔案，建議加入 `.gitignore`。

---

## 使用方式 (Usage)

### TUI 操作流程

1. **專案選擇畫面** — 選擇或管理專案
   - `N` 新建 / `C` 複製 / `O` 開啟 / `D` 刪除
   - `I` 匯入設定 / `E` 匯出設定 / `?` 說明 / `X` 離開

2. **物件選擇畫面 (Table/View/SP...)** — 選取要複製的資料庫物件
   - **分頁切換**：`1` Tables / `2` Views / `3` Stored Procedures / `4` Functions / `5` Triggers
   - `Space` 選取 / `A` 全選 / `F` 篩選條件 / `P` PII 規則
   - `Ctrl+O` 返回專案 / `S` 儲存 / `X` 匯出設定檔 / `G` 開始複製 / `Q` 離開

3. **執行複製** — 批次讀取、去敏化、寫入目標資料庫

### Demo 模式
模擬複製過程而不實際連接資料庫：
```bash
python db_replicator.py --demo
```

### 無人值守模式 (Headless Mode)

使用預先導出的 Deploy Profile 執行自動化部署，跳過 TUI 互動：
```bash
python db_replicator.py --deploy-profile my_project_profile.json \
  --src-pwd "source_password" --tgt-pwd "target_password"
```
或者使用環境變數：
```bash
export SRC_DB_PWD="your_password"
python db_replicator.py --deploy-profile my_project_profile.json
```

### DDL 備份 (Backup DDL)

將 Source DB 的 View / Stored Procedure / Function / Trigger 匯出成一物件一檔的
`.sql` 快照，讓備份目錄成為**獨立的 git repo**，以 `git diff` 追蹤 DDL 變更歷史。

```bash
# 首次使用：備份目錄必須由使用者自行 git init（工具對 git 唯讀）
mkdir -p sql_backup/HRM && git -C sql_backup/HRM init

# 先試跑，確認會新增/更新/刪除哪些檔案
python db_replicator.py --backup-ddl HRM --dry-run \
  --src-server 172.22.1.34 --src-database hrm --src-uid sa --src-pwd "password"

# 實際執行
python db_replicator.py --backup-ddl HRM \
  --src-server 172.22.1.34 --src-database hrm --src-uid sa --src-pwd "password"

# 檢視變更後提交
cd sql_backup/HRM && git diff && git add -A && git commit -m "DDL snapshot"
```

僅需 Source 連線，不會連線或寫入 Target。輸出結構：

```
sql_backup/HRM/            ← 獨立 git repo，外層 .gitignore 已排除 sql_backup/
├── README.md              ← 首次執行產生的 HOWTO，供 AI Agent 改寫為正式說明
├── _manifest.json         ← 執行資訊與 skipped 清單（已 gitignore）
├── Views/dbo.V_Emp.sql
├── Functions/dbo.fn_Bar.sql
├── StoredProcedures/dbo.usp_Foo.sql
└── Triggers/dbo.TRG_Baz.sql
```

**行為說明**

| 項目 | 說明 |
|:---|:---|
| 備份範圍 | 全庫，四類 programmable objects。**不含 table 結構** |
| DDL 內容 | 來自 `sys.sql_modules`，開頭動詞正規化為 `CREATE OR ALTER`（需 SQL Server 2016 SP1+），換行統一 LF。**不做** DB 名稱替換 |
| 刪除同步 | 來源已不存在的物件，其 `.sql` 檔會被刪除，git 才看得出物件被移除 |
| 加密 / CLR 物件 | 無 T-SQL 定義，既不寫入也不刪除既有檔案，列入報表的 skipped |
| 中止保護 | 執行前檢查備份目錄是否為 git repo、working tree 是否乾淨（可用 `--allow-dirty` 略過）|
| 失敗處理 | 抓取為單一 query，失敗時完全不動檔案；落檔階段先寫入更新、最後才刪除，崩潰只會留下多餘檔而非缺檔 |

**不涵蓋**：物件權限、extended properties、synonym、使用者自訂型別、sequence、
以及所有 table 結構。這不是災難復原用的備份。

`.sql` 檔可能含有內部主機名、IP 與商業邏輯，請確認備份 repo 的存放位置合乎資安規範。

## 設定與客制化 (Configuration)

所有設定透過 TUI 內的專案管理進行，儲存於 `config.db`：

- **LARGE_TABLE_FILTERS**: 設定大表篩選條件 (如只複製近兩年資料)
- **SENSITIVE_COLUMNS**: 定義去識別化規則
  - `obfuscate_name`: 姓名混淆 (Seed: emp_no)
  - `obfuscate_spouse_name`: 配偶姓名 (Seed: emp_no + salt)
  - `obfuscate_phone`: 電話遮罩 (末5碼亂數)
  - `obfuscate_address`: 地址混淆
  - `anonymize_id`: 身分證遮罩
  - `clear_content`: 直接清空內容

## 資料庫字庫快取
首次執行時，程式會分析 `EMP_DATA` 建立姓名混淆字庫並儲存於 `OBFUSCATE_NAME.json`。若需重置字庫，只需刪除該 JSON 檔案即可。
