# QA Checklist

## 基礎驗證
- [ ] 後端 `python -m pytest` 全部通過。
- [ ] 前端 `npm test -- --watchAll=false --runTestsByPath src/Page1.test.tsx` 通過。
- [ ] 使用 `.env` 內的 Azure 金鑰能成功啟動服務，無認證錯誤。

## 功能檢查
- [ ] 前端可載入指定資料夾的 PDF 清單，無錯誤訊息。
- [ ] 勾選檔案後按「開始分析」，進度條會更新、顯示目前檔名、訊息列表出現樣板/DI 提示。
- [ ] 分析完成後可下載 Excel，欄位與數值正確，缺漏欄位具淡黃色底色。
- [ ] 「終止分析」會讓工作狀態變為 `cancelled` 並停止產出結果。
- [ ] 最近路徑快捷按鈕會顯示最新 5 筆，且可快速重新載入。

## 錯誤處理
- [ ] 不存在的路徑顯示友善錯誤訊息（404 或權限提示）。
- [ ] Azure 服務回傳異常時，前端提示並可重新嘗試。
- [ ] vLLM 未設定時，系統自動退回 Azure OpenAI 或啟發式，不會中斷流程。

## 效能與穩定性
- [ ] 佇列同時提交 3 個以上工作，僅限 `ANALYSIS_MAX_CONCURRENT` 的數量同時執行，其餘保持 queued。
- [ ] 大型 PDF（>10MB）處理時間允許在預期範圍，無記憶體爆炸或 timeout。
- [ ] job_runs 內產生的暫存檔案可被清除，不會鎖住資源。

## 文件與交付
- [ ] `docs/api.md`、`docs/runbook.md`、`docs/qa-checklist.md` 與實作一致。
- [ ] README 或專案說明包含啟動、測試、部署指引。
- [ ] 重要變更記錄在版本控管（Git）中，便於追蹤。
