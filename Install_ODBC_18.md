# 在 Ubuntu/Debian 上安裝 ODBC Driver 18 for SQL Server

這份文件將引導您如何在 Ubuntu/Debian 系統上安裝 `ODBC Driver 18 for SQL Server`。

## 安裝步驟

請依照以下步驟執行：

### 1. 匯入公用存放庫 GPG 金鑰

這個指令會下載 Microsoft GPG 金鑰並將其新增到您系統的信任金鑰中，以便安全地從 Microsoft 的存放庫下載套件。

```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
```

### 2. 註冊 Microsoft Ubuntu 存放庫

這個指令會將 Microsoft SQL Server 存放庫新增到您系統的軟體來源清單中。

```bash
sudo bash -c "curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list"
```

### 3. 更新套件清單

這個指令會重新整理您系統的套件索引，以包含新加入的 Microsoft 存放庫。

```bash
sudo apt-get update
```

### 4. 安裝 ODBC 驅動程式及其相依套件

這個指令會安裝 `msodbcsql18` 套件 (ODBC Driver 18 for SQL Server) 和 `unixodbc-dev` (unixODBC 驅動程式管理員的開發標頭檔)。

```bash
sudo apt-get install -y msodbcsql18 unixodbc-dev
```

完成以上步驟後，`ODBC Driver 18 for SQL Server` 就會成功安裝在您的系統上。
