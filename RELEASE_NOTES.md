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