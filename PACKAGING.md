# Packaging Guide (Windows, macOS, Linux)

PyInstaller creates executables for the **OS it is running on**. To build for all three platforms, the best approach is to use **GitHub Actions** with a "build matrix".

## 🚀 Unified GitHub Action Workflow

Create or update `.github/workflows/build_all.yml`. This single file will generate `.exe` (Windows), binary (Linux), and Mach-O (macOS) executables automatically.

```yaml
name: Build All Platforms

on:
  push:
    branches: [ "main" ]
  workflow_dispatch:

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: windows-latest
            artifact_name: DB_Cloner_Windows.exe
            asset_name: DB_Cloner_Windows.exe
          - os: macos-latest
            artifact_name: DB_Cloner_macOS
            asset_name: DB_Cloner_macOS
          - os: ubuntu-latest
            artifact_name: DB_Cloner_Linux
            asset_name: DB_Cloner_Linux

    runs-on: ${{ matrix.os }}
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
        
    - name: Install Dependencies
      # Install system-level dependencies for Linux if needed (e.g. odbc headers)
      run: |
        pip install pyinstaller
        pip install sqlalchemy pandas pyodbc tqdm textual
        
    - name: Build with PyInstaller
      run: |
        pyinstaller --onefile --name ${{ matrix.asset_name }} --clean db_replicator.py
        
    - name: Upload Artifact
      uses: actions/upload-artifact@v4
      with:
        name: ${{ matrix.artifact_name }}
        path: dist/${{ matrix.asset_name }}*
```

---

## 💻 Manual Builds (Local)

If you ignore GitHub Actions, you must build on the specific machine:

###  macOS (Your current machine)
1. **Run:** `pyinstaller --onefile --name DB_Cloner_Mac db_replicator.py`
2. **Output:** `dist/DB_Cloner_Mac`
3. **Note:** When sending this file to other Macs, they might get a "Unidentified Developer" warning. They can bypass this by Right-Click > Open.

### 🐧 Linux
1. You need a Linux environment (or Docker container).
2. **Run:** `pyinstaller --onefile --name DB_Cloner_Linux db_replicator.py`
3. **Note:** Ideally build on an older Linux version (like Ubuntu 20.04) to ensure compatibility with newer systems (glibc versioning).

---

## ⚠️ CRITICAL: ODBC Driver Deps (All Platforms)

**The Driver is NOT included in the executable.**

| Platform | Requirement |
| :--- | :--- |
| **Windows** | User must install `ODBC Driver 18 for SQL Server` (msi). |
| **macOS** | User must install via Homebrew: `brew install msodbcsql18` |
| **Linux** | User must install the Microsoft ODBC driver for their specific distro (apt-get/yum). |

If the driver is missing, the app will crash with "Data source name not found".
