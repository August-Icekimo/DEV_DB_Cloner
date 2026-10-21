/* ============================================================================
 * gen_default_constraints.sql  —  DEFAULT 約束補齊腳本「產生器」
 *
 * 背景：DEV_DB_Cloner 建表時只帶型別與 NULL/NOT NULL，不帶 DEFAULT，
 *       clone 出來的測試庫遇到「省略欄位、靠預設值補值」的 INSERT 會 Msg 515。
 *       （2026-09-23 hrm_0907 SRB1000 出帳踩到：SALARY_MONTH.bonus_no）
 *
 * 用法：
 *   1. 在【來源庫】執行本腳本（UAT 172.22.1.130 / hrm_test），只讀、不改任何東西。
 *   2. 結果每列一句 T-SQL，整欄複製（或用 sqlcmd 輸出成檔）。
 *   3. 在【目標測試庫】執行輸出內容（單一批次，不需 GO）。
 *
 *   建議用 sqlcmd 輸出成 UTF-8 檔，避免 SSMS 複製時中文常值變 ???：
 *     sqlcmd -S 172.22.1.130 -d hrm_test -U <user> -P <pwd> -i gen_default_constraints.sql \
 *            -o apply_defaults.sql -h -1 -y 8000 -f 65001
 *     sqlcmd -S <目標> -d <測試庫> -U <user> -P <pwd> -i apply_defaults.sql -f 65001
 *
 * 產出的腳本特性（可重複執行）：
 *   - 目標端表或欄位不存在 → 略過（計入 skipped）
 *   - 目標端該欄已有任何 DEFAULT → 略過（以欄位判斷，不以約束名稱判斷）
 *   - 來源為系統命名（DF__xxx__yyy__1A2B3C）或名稱在目標端已被占用 → 建成未命名約束
 *   - 每句獨立 TRY/CATCH，單句失敗不中斷，最後印出 added / skipped / failed 統計
 *   - 只補 DEFAULT，不回填既有 NULL 資料
 * ========================================================================== */
SET NOCOUNT ON;

WITH src AS (
    SELECT
        ord        = ROW_NUMBER() OVER (ORDER BY s.name, t.name, c.column_id),
        tbl2part   = QUOTENAME(s.name) + N'.' + QUOTENAME(t.name),
        tblLit     = REPLACE(QUOTENAME(s.name) + N'.' + QUOTENAME(t.name), N'''', N''''''),
        colQ       = QUOTENAME(c.name),
        colLit     = REPLACE(c.name, N'''', N''''''),
        dfQ        = QUOTENAME(dc.name),
        dfLit      = REPLACE(QUOTENAME(s.name) + N'.' + QUOTENAME(dc.name), N'''', N''''''),
        dfDef      = dc.definition,
        sysNamed   = dc.is_system_named
    FROM sys.default_constraints dc
    JOIN sys.tables  t ON t.object_id = dc.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.columns c ON c.object_id = dc.parent_object_id
                      AND c.column_id = dc.parent_column_id
    WHERE t.is_ms_shipped = 0
)
SELECT stmt
FROM (
    SELECT 0 AS ord, N'SET NOCOUNT ON; DECLARE @added int = 0, @skipped int = 0, @failed int = 0;' AS stmt
    UNION ALL
    SELECT ord,
        CONCAT(
            N'IF OBJECT_ID(N''', tblLit, N''', ''U'') IS NULL OR COL_LENGTH(N''', tblLit, N''', N''', colLit, N''') IS NULL',
            N' OR EXISTS (SELECT 1 FROM sys.default_constraints WHERE parent_object_id = OBJECT_ID(N''', tblLit, N''')',
            N' AND parent_column_id = COLUMNPROPERTY(OBJECT_ID(N''', tblLit, N'''), N''', colLit, N''', ''ColumnId''))',
            N' SET @skipped += 1;',
            N' ELSE BEGIN BEGIN TRY',
            CASE WHEN sysNamed = 1
                 THEN CONCAT(N' ALTER TABLE ', tbl2part, N' ADD DEFAULT ', dfDef, N' FOR ', colQ, N';')
                 ELSE CONCAT(
                        N' IF OBJECT_ID(N''', dfLit, N''') IS NULL',
                        N' ALTER TABLE ', tbl2part, N' ADD CONSTRAINT ', dfQ, N' DEFAULT ', dfDef, N' FOR ', colQ, N';',
                        N' ELSE ALTER TABLE ', tbl2part, N' ADD DEFAULT ', dfDef, N' FOR ', colQ, N';')
            END,
            N' SET @added += 1;',
            N' END TRY BEGIN CATCH SET @failed += 1;',
            N' PRINT CONCAT(N''FAIL ', tblLit, N'.', REPLACE(colQ, N'''', N''''''), N': '', ERROR_MESSAGE()); END CATCH END;'
        )
    FROM src
    UNION ALL
    SELECT 2147483647,
        N'DECLARE @total int = (SELECT COUNT(*) FROM sys.default_constraints);'
      + N' PRINT CONCAT(N''DEFAULT 補齊完成：added='', @added, N'', skipped='', @skipped, N'', failed='', @failed, N'', 目前 DEFAULT 總數='', @total);'
) x
ORDER BY ord;
