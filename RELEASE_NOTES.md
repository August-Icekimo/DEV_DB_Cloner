
# 首次提交 / 版本發佈 v1.0.0 (First Commit / Release v1.0.0)

## 第一次寫標題，不知道寫什麼好 (Title Unk)
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

### 環境需求 (Prerequisites)
* Python 3.x
* ODBC Driver 18 for SQL Server

# First Commit / Release v1.0.0

## Title
feat: initial release of HRM DB Replicator with smart anonymization

## Description
This release introduces the `DEV_DB_Cloner` tool, a robust Python-based utility for replicating SQL Server databases with integrated PII protection.

### Key Capabilities:
1. **Interactive Selection**: Textual-based TUI for easy table selection.
2. **Advanced Anonymization Engine**:
   - `obfuscate_name`: Context-aware Chinese name generation based on character counts (2/3/4 chars).
   - `obfuscate_spouse_name`: Salted logic to ensure spouse names differ from employee names.
   - `obfuscate_address`: Full-width digit normalization and realistic address structure generation.
   - `obfuscate_phone`: Preserves strict formatting while analyzing and masking the last 5 digits.
   - `clear_content`: Securely scrubs highly sensitive fields (e.g., English Names).
3. **Audit & Traceability**:
   - **File Logging**: Generates daily log files (`YYYYMMDD_Clone.log`) with detailed execution steps and data samples.
   - **Date-based Salt**: Injects execution date into anonymization seeds to ensure consistent results within the same day while varying across different days.
4. **Data Integrity**:
   - Enforces `NVARCHAR` mapping for all string columns to resolve Chinese encoding issues on SQL Server.
   - Auto-initializes name corpus from source `EMP_DATA` for realistic test data generation.
4. **Performance**: Batch processing with `tqdm` progress visualization.

### Configuration
- Pre-configured `SENSITIVE_COLUMNS` map for `EMP_DATA`, `ADVANCE_BONUS_GRANT`, and `DEPENDENT_DATA`.
- Built-in filtering for large tables (e.g., Logs, Attendance) to optimize sync time.

### Prerequisites
- Python 3.x
- ODBC Driver 18 for SQL Server