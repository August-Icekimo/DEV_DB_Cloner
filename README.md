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
- **Unicode 支援**: 強制修正編碼問題 (NVARCHAR)，確保中文資料正確寫入。
- **高效傳輸**: 支援分批次 (Batch) 讀取與寫入，並顯示進度條。

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

2. **資料表選擇畫面** — 選取要複製的資料表
   - `Space` 選取 / `A` 全選 / `F` 篩選條件 / `P` PII 規則
   - `S` 儲存 / `G` 開始複製 / `Q` 離開

3. **執行複製** — 批次讀取、去敏化、寫入目標資料庫

### Demo 模式
模擬複製過程而不實際連接資料庫：
```bash
python db_replicator.py --demo
```

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
