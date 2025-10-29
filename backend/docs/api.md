# ASUS Label 分析後端 API 與設定

## 執行環境設定
- `ANALYSIS_JOBS_DIR`：分析輸出資料夾路徑，預設為 `backend/job_runs`。
- `ANALYSIS_MAX_CONCURRENT`：同時可以執行的分析工作數，預設 2。
- `VLLM_BASE_URL` / `VLLM_MODEL`：啟用 vLLM 分析引擎所需的服務端點與模型名稱；未設定時會 fallback 至 Azure OpenAI，再退回啟發式引擎。
- `.env` 其餘欄位：Azure OpenAI、Azure Document Intelligence 所需的金鑰與端點。

## 主要 API

### `POST /api/list-pdfs`
- 功能：列出目標資料夾下所有 `.pdf` 檔案。
- 請求：`{ "path": "O:\\AI\\..." }`
- 回應：`[{ "id": 1, "filename": "sample.pdf", "is_label": false }, ...]`
- 可能錯誤：`404`（路徑不存在）、`400`（非資料夾）、`500`（讀取失敗）。

### `POST /api/analyze/start`
- 功能：建立分析工作並排入佇列。
- 請求：
  ```json
  {
    "path": "O:\\AI\\...",
    "files": [{ "id": 1, "filename": "battery.pdf", "is_label": false }],
    "label_filename": "label.pdf"
  }
  ```
- 回應：`{ "job_id": "hex...", "status": "queued" }`
- 佇列：伺服器會依 `ANALYSIS_MAX_CONCURRENT` 控制同時執行的工作數，其餘任務維持在 queued 狀態。

### `GET /api/analyze/status/{job_id}`
- 功能：查詢分析進度。
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
    "messages": ["Job queued for processing", "..."]
  }
  ```

### `POST /api/analyze/stop/{job_id}`
- 功能：要求停止佇列中或執行中的分析。
- 回應：與 `status` 相同格式，`status` 會是 `cancelled` 並保留目前進度。

### `GET /api/analyze/download/{job_id}`
- 功能：下載分析結果 Excel，僅在 `download_ready = true` 時可用。
- 回應：`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`；若檔案未就緒回傳 `404`。

## 分析流程概述
1. 排隊後進入執行：每個工作取得執行權時才會進入 `running` 狀態。
2. 依傳入檔案順序處理 PDF。若提供 `label_filename`，會將標籤檔的欄位做為後續補值。
3. 優先使用 vLLM 提取欄位；若未設定 vLLM 端點則自動切換至 Azure OpenAI，再 fallback 到啟發式分析。
4. 完成後產生 `analysis_result.xlsx` 於對應 job 目錄，可透過下載 API 取得。
