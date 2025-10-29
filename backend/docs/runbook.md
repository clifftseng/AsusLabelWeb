# ASUS Label 系統 Runbook

## 1. 啟動前檢查
- 確認 `.env` 已填入 Azure OpenAI 與 Azure Document Intelligence 的 URL 與金鑰。
- 檢查 `ANALYSIS_FORMAT_DIR` 是否指向可讀取的 JSON 樣板資料夾；若未設定會使用 `backend/formats`。
- 建議於虛擬環境執行 `pip install -r backend/requirements.txt`，確保 azure-ai-formrecognizer 等套件就緒。
- 前端需安裝依賴：`cd frontend && npm install`。

## 2. 後端啟動
```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```
- 預設會在 `backend/job_runs` 產出 Excel 與暫存資料；可透過 `ANALYSIS_JOBS_DIR` 覆寫。

## 3. 前端啟動
```bash
cd frontend
npm start
```
- `.env` 中的 `REACT_APP_API_BASE_URL` 須指向後端 API，例如 `http://localhost:8000`。

## 4. 常見操作
- 使用者流程：輸入來源路徑 → 載入 PDF → 勾選檔案 → 開始分析 → 觀察進度 → 下載 Excel。
- UI 會記錄最近 5 筆路徑並列於按鈕，可快速切換常用資料夾。
- 若要強制終止工作，可於進度區塊按「終止分析」，後端會即時取消佇列中的任務。

## 5. 疑難排解
- **載入 PDF 失敗**：檢查路徑是否存在／權限足夠；系統訊息會顯示 404 或 500。
- **分析進度卡住**：查看 `backend/job_runs/<job_id>/` 是否有部分 Excel；必要時可呼叫 `/api/analyze/stop/{job}`。
- **Azure 認證錯誤**：後端啟動時會於日誌中顯示 `Document Intelligence 環境變數未設定` 或 `azure-ai-formrecognizer 套件未安裝`。
- **Excel 未著色**：確保 `openpyxl` 正確安裝；若缺少會於 `main.py` raise `RuntimeError`。

## 6. 例行維運
- 定期清理 `backend/job_runs` 舊資料，避免磁碟爆滿。
- 監控 Azure OpenAI/DI 用量，避免超出配額造成 429/401。
- 任何套件更新需重新執行 `pip install -r backend/requirements.txt` 並跑 `pytest` 確認。

## 7. 手動驗證流程
1. 取得樣本 PDF 至指定的網路或本機路徑。
2. 使用前端載入與分析，確認：
   - 進度條從 0→100%
   - 進度訊息包含樣板／Document Intelligence 提示
   - Excel 匯出並含欄位著色
3. 嘗試終止分析，觀察狀態轉為 `cancelled` 並且 Excel 不產出。
4. 檢查後端日誌是否有例外狀況。

## 8. 系統更新流程
- 建議流程：`git pull` → `pip install -r backend/requirements.txt` → `npm install` → `pytest` → 部署。
- 若更新涉及 `.env`，請同步更新作業環境並重啟後端服務。
