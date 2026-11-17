/* ============================================================================
 * gen_identity_sequences.sql  —  IDENTITY 自動編號補齊腳本「產生器」
 *
 * 背景：DEV_DB_Cloner 2026-10-21 之前建表時刻意不帶 IDENTITY，clone 出來的測試庫
 *       全庫沒有任何 IDENTITY 欄位，省略該欄的 INSERT 會 Msg 515。
 *       （2026-10 hrm_0907 SYS_TRANS_LOG.seq_no：交易 log 自 clone 後全部寫入失敗）
 *       既有欄位無法事後 ALTER 成 IDENTITY，改以 SEQUENCE + DEFAULT (NEXT VALUE FOR ...)
 *       達到「省略該欄時自動編號」的效果。HRM 程式沒有用 SCOPE_IDENTITY／@@IDENTITY／
 *       IDENT_CURRENT，所以這樣就夠了；重新 clone 的庫會直接帶 IDENTITY，不需要本腳本。
 *
 * 用法：
 *   1. 在【來源庫】執行本腳本（UAT 172.22.1.130 / hrm_test），只讀、不改任何東西。
 *   2. 結果每列一句 T-SQL，整欄複製（或用 sqlcmd 輸出成檔）。
 *   3. 在【目標測試庫】執行輸出內容（單一批次，不需 GO）。
 *
 *   建議用 sqlcmd 輸出成 UTF-8 檔：
 *     sqlcmd -S 172.22.1.130 -d hrm_test -U <user> -P <pwd> -i gen_identity_sequences.sql \
 *            -o apply_identity_sequences.sql -h -1 -y 8000 -f 65001
 *     sqlcmd -S <目標> -d <測試庫> -U <user> -P <pwd> -i apply_identity_sequences.sql -f 65001
 *
 * 對每個來源 IDENTITY 欄位，產出的腳本在目標端：
 *   - 建 SEQUENCE <schema>.SEQ_<table>，型別同欄位，INCREMENT BY 同來源 increment；
 *     START 在目標端執行當下計算：ISNULL(MAX(col), seed - increment) + increment
 *     （increment 為負時改用 MIN），所以會接在目標端現有資料之後
 *   - ADD CONSTRAINT DF_<table>_<col> DEFAULT (NEXT VALUE FOR <schema>.SEQ_<table>) FOR <col>
 *
 * 產出的腳本特性（可重複執行）：
 *   - 目標端表或欄位不存在 → 略過（計入 skipped）
 *   - 目標端該欄已是 IDENTITY（以新版 cloner 重新 clone 過）→ 略過
 *   - 目標端該欄已有任何 DEFAULT（例如手動補過的 SYS_TRANS_LOG）→ 略過（以欄位判斷，不以名稱判斷）
 *   - SEQUENCE 已存在（上次執行到一半失敗）→ 不重建，RESTART WITH 重新計算的起始值
 *   - 約束名稱在目標端已被占用 → 建成未命名約束
 *   - 每句獨立 TRY/CATCH，單句失敗不中斷，最後印出 added / skipped / failed 統計
 *   - CREATE SEQUENCE 與 ALTER TABLE 都走 dynamic SQL：START 值要執行時才知道，
 *     而且同一批次裡 NEXT VALUE FOR 參照的 SEQUENCE 在編譯時還不存在
 * ========================================================================== */
SET NOCOUNT ON;

WITH src AS (
    SELECT
        ord      = ROW_NUMBER() OVER (ORDER BY s.name, t.name),
        tbl2part = QUOTENAME(s.name) + N'.' + QUOTENAME(t.name),
        tblLit   = REPLACE(QUOTENAME(s.name) + N'.' + QUOTENAME(t.name), N'''', N''''''),
        colQ     = QUOTENAME(c.name),
        colLit   = REPLACE(c.name, N'''', N''''''),
        colQLit  = REPLACE(QUOTENAME(c.name), N'''', N''''''),
        seqLit   = REPLACE(QUOTENAME(s.name) + N'.' + QUOTENAME(LEFT(N'SEQ_' + t.name, 128)), N'''', N''''''),
        dfQLit   = REPLACE(QUOTENAME(LEFT(N'DF_' + t.name + N'_' + c.name, 128)), N'''', N''''''),
        dfLit    = REPLACE(QUOTENAME(s.name) + N'.' + QUOTENAME(LEFT(N'DF_' + t.name + N'_' + c.name, 128)), N'''', N''''''),
        -- 用基底系統型別：欄位若是 alias type，SEQUENCE 也能建
        seqType  = CASE WHEN TYPE_NAME(c.system_type_id) IN (N'decimal', N'numeric')
                        THEN CONCAT(TYPE_NAME(c.system_type_id), N'(', c.precision, N',0)')
                        ELSE TYPE_NAME(c.system_type_id) END,
        seed     = CONVERT(nvarchar(40), ic.seed_value),
        incr     = CONVERT(nvarchar(40), ic.increment_value),
        agg      = CASE WHEN CONVERT(numeric(38,0), ic.increment_value) < 0 THEN N'MIN' ELSE N'MAX' END
    FROM sys.identity_columns ic
    JOIN sys.tables  t ON t.object_id = ic.object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.columns c ON c.object_id = ic.object_id
                      AND c.column_id = ic.column_id
    WHERE t.is_ms_shipped = 0
)
SELECT stmt
FROM (
    SELECT 0 AS ord, N'SET NOCOUNT ON; DECLARE @added int = 0, @skipped int = 0, @failed int = 0, @start numeric(38,0), @sql nvarchar(max);' AS stmt
    UNION ALL
    SELECT ord,
        CONCAT(
            N'IF OBJECT_ID(N''', tblLit, N''', ''U'') IS NULL OR COL_LENGTH(N''', tblLit, N''', N''', colLit, N''') IS NULL',
            N' OR COLUMNPROPERTY(OBJECT_ID(N''', tblLit, N'''), N''', colLit, N''', ''IsIdentity'') = 1',
            N' OR EXISTS (SELECT 1 FROM sys.default_constraints WHERE parent_object_id = OBJECT_ID(N''', tblLit, N''')',
            N' AND parent_column_id = COLUMNPROPERTY(OBJECT_ID(N''', tblLit, N'''), N''', colLit, N''', ''ColumnId''))',
            N' SET @skipped += 1;',
            N' ELSE BEGIN BEGIN TRY',
            N' SELECT @start = ISNULL(CONVERT(numeric(38,0), ', agg, N'(', colQ, N')), CONVERT(numeric(38,0), ', seed, N') - (', incr, N')) + (', incr, N') FROM ', tbl2part, N';',
            N' IF OBJECT_ID(N''', seqLit, N''', ''SO'') IS NULL',
            N' SET @sql = N''CREATE SEQUENCE ', seqLit, N' AS ', seqType,
            N' START WITH '' + CONVERT(nvarchar(40), @start) + N'' INCREMENT BY ', incr, N';'';',
            N' ELSE SET @sql = N''ALTER SEQUENCE ', seqLit, N' RESTART WITH '' + CONVERT(nvarchar(40), @start) + N'';'';',
            N' EXEC (@sql);',
            N' IF OBJECT_ID(N''', dfLit, N''') IS NULL',
            N' SET @sql = N''ALTER TABLE ', tblLit, N' ADD CONSTRAINT ', dfQLit, N' DEFAULT (NEXT VALUE FOR ', seqLit, N') FOR ', colQLit, N';'';',
            N' ELSE SET @sql = N''ALTER TABLE ', tblLit, N' ADD DEFAULT (NEXT VALUE FOR ', seqLit, N') FOR ', colQLit, N';'';',
            N' EXEC (@sql);',
            N' SET @added += 1;',
            N' END TRY BEGIN CATCH SET @failed += 1;',
            N' PRINT CONCAT(N''FAIL ', tblLit, N'.', colQLit, N': '', ERROR_MESSAGE()); END CATCH END;'
        )
    FROM src
    UNION ALL
    SELECT 2147483647,
        N'DECLARE @total int = (SELECT COUNT(*) FROM sys.sequences);'
      + N' PRINT CONCAT(N''IDENTITY 自動編號補齊完成：added='', @added, N'', skipped='', @skipped, N'', failed='', @failed, N'', 目前 SEQUENCE 總數='', @total);'
) x
ORDER BY ord;
