# HRM Database Replicator & Anonymizer (DEV_DB_Cloner)

一個 Python 專案，用於將來源 SQL Server 資料庫複製到目標開發環境，並由內建的去識別化引擎自動處理敏感個資 (PII)。

## 主要功能 (Key Features)

- **互動式 TUI 介面**: 使用 Textual 框架提供終端機圖形介面，方便選擇要複製的資料表。
- **自動去識別化 (Smart Anonymization)**:
  - **姓名混淆**: 基於真實姓名統計資料庫或來源資料庫動態產生的字庫，並支援配偶姓名區隔邏輯。
  - **地址處理**: 支援全形轉半形、縣市識別，並隨機產生行政區與門牌號碼。
  - **身分證/電話**: 自動遮罩身分證字號與手機號碼 (保留末碼格式)。
  - **內容清除**: 針對高敏感欄位 (如英文姓名) 提供直接清空功能。
- **稽核與追蹤 (Audit & Traceability)**:
  - **日誌記錄 (Logging)**: 自動產生 `YYYYMMDD_Clone.log`，詳實記錄執行過程與 Before/After 樣本，供稽核比對。
  - **動態 Salt (Date-Based Salt)**: 依據執行日期 (`YYYYMMDD`) 動態產生混淆種子，確保當日執行結果一致，但不同日結果不同，提升安全性並支援版本對照。
- **Unicode 支援**: 強制修正編碼問題 (NVARCHAR)，確保中文資料正確寫入。
- **彈性配置**: 支援針對特定 Table 設定篩選條件 (`LARGE_TABLE_FILTERS`) 與去識別化規則 (`SENSITIVE_COLUMNS`)。
- **高效傳輸**: 支援分批次 (Batch) 讀取與寫入，並顯示進度條。

## 系統需求 (Requirements)

- Python 3.9+
- Microsoft ODBC Driver 18 for SQL Server
- Target Database: SQL Server (Docker or Local)

## 安裝 (Installation)

1. 安裝 ODBC Driver 18 (參考 `Install_ODBC_18.md`)
2. 建立 Python 虛擬環境並安裝套件:
   ```bash
   python -m venv .clone.venv
   source .clone.venv/bin/activate  # Linux
   pip install -r requirements.txt
   ```

## 使用方式 (Usage)

### 1. 執行複製工具
預設會開啟 TUI 介面供使用者選擇資料表：
```bash
python db_replicator.py
```

### 2. 指定連線資訊 (CLI Arguments)
可透過參數直接指定來源與目標資料庫：
```bash
python db_replicator.py \
  --src-server 172.22.1.34 --src-database hrm --src-uid sa --src-pwd "password" \
  --tgt-server localhost --tgt-database hrm_dev --tgt-uid sa --tgt-pwd "password"
```

### 3. Demo 模式
模擬複製過程而不實際寫入資料庫：
```bash
python db_replicator.py --demo
```

## 設定與客制化 (Configuration)

工具內建配置 (`db_replicator.py`):

- **LARGE_TABLE_FILTERS**: 設定大表篩選條件 (如只複製近兩年資料)。
- **SENSITIVE_COLUMNS**: 定義去識別化規則。
  - `obfuscate_name`: 姓名混淆 (Seed: emp_no)
  - `obfuscate_spouse_name`: 配偶姓名 (Seed: emp_no + salt)
  - `obfuscate_phone`: 電話遮罩 (末5碼亂數)
  - `obfuscate_address`: 地址混淆
  - `anonymize_id`: 身分證遮罩
  - `clear_content`: 直接清空內容

## 資料庫字庫快取
首次執行時，程式會分析 `EMP_DATA` 建立姓名混淆字庫並儲存於 `OBFUSCATE_NAME.json`。若需重置字庫，只需刪除該 JSON 檔案即可。
