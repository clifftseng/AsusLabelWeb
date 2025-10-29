# ASUS Label 分析後端 API 與設定指南

## 執行環境設定
- `.env`：啟動時會自動載入，請在此填入所有金鑰與端點。
  - `AZURE_OPENAI_*`：Azure OpenAI Chat Completions。
  - `DOCUMENT_INTELLIGENCE_*`：Azure Document Intelligence。
- `ANALYSIS_JOBS_DIR`：分析輸出資料夾路徑，預設為 `backend/job_runs`。
- `ANALYSIS_MAX_CONCURRENT`：可同時執行的分析工作數量，預設為 2。
- `ANALYSIS_FORMAT_DIR`：自訂 PDF 樣板(JSON)的位置；若未設定，會使用 `backend/formats`。
- `VLLM_BASE_URL` / `VLLM_MODEL`：vLLM 服務端點與模型名稱，若未設定會自動退回 Azure OpenAI，再退回啟發式解析。

## API 一覽

### `POST /api/list-pdfs`
- 功能：列出指定路徑下第一層的 `.pdf` 檔案。
- 請求範例：`{ "path": "O:\\AI\\..." }`
- 回應範例：`[{ "id": 1, "filename": "sample.pdf" }, ...]`
- 可能錯誤：`404`（路徑不存在）、`400`（非資料夾）、`500`（讀取失敗）。

### `POST /api/analyze/start`
- 功能：建立分析工作並排入佇列。
- 請求範例：
  ```json
  {
    "path": "O:\\AI\\...",
    "files": [{ "id": 1, "filename": "battery.pdf" }]
  }
  ```
- 回應範例：`{ "job_id": "hex...", "status": "queued" }`
- 說明：伺服器依 `ANALYSIS_MAX_CONCURRENT` 控制同時執行的工作數，其餘任務保持在 queued 狀態。

### `GET /api/analyze/status/{job_id}`
- 功能：查詢分析進度與部份結果。
- 回應範例：
  ```json
  {
    "job_id": "hex...",
    "status": "running",
    "progress": 33.0,
    "processed_count": 1,
    "total_count": 3,
    "results": [...],
    "download_ready": false,
    "download_path": null,
    "current_file": "battery.pdf",
    "messages": ["工作已加入佇列，等待開始。", "..."]
  }
  ```

### `POST /api/analyze/stop/{job_id}`
- 功能：要求停止佇列中的工作或終止正在進行的分析。
- 回應：與 `status` 相同格式，`status` 會為 `cancelled` 並保留目前進度。

### `GET /api/analyze/download/{job_id}`
- 功能：下載分析結果 Excel，僅在 `download_ready = true` 時可用。
- 回應：`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`；若檔案未就緒回傳 `404`。

## 分析流程摘要
1. 任務建立後即排入佇列，取得執行權時狀態改為 `running`。
2. 依傳入檔案順序逐一解析 PDF，並於進度 API 回傳已完成的部分結果、目前檔名與進度百分比。
3. 若在 `ANALYSIS_FORMAT_DIR` 找到對應樣板，會優先截取樣板定義的區塊當作提示，再交由分析引擎產生完整欄位；進度訊息會加註該步驟。
4. 引擎優先使用 vLLM；若無設定則退回 Azure OpenAI，再退回啟發式規則。
5. 全部完成後產生 `analysis_result.xlsx`，可透過下載 API 取得，也能從進度 API 看到可下載狀態與訊息。

## 前端整合重點
- 使用者可在介面上的「來源資料夾」欄位輸入 UNC 或本機路徑，系統會記錄最近 5 筆路徑以供快速選取。
- 「分析進度」區塊會同步顯示後端回傳的訊息（如樣板命中、分析完成等），並於完成後提供 Excel 下載連結。
