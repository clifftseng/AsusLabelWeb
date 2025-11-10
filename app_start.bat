@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo [INFO] 進入啟動腳本...

REM 1) 修正：根路徑一定要是 52_AsusLabel
set "ROOT=D:\workingarea\52_AsusLabel"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "REACT_APP_API_BASE_URL=http://10.100.101.57:8080"
REM 2) Windows venv 啟用檔名是 activate.bat
set "VENV_ACTIVATE=%BACKEND_DIR%\.venv\Scripts\activate.bat"
set "LOG_DIR=%ROOT%\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [INFO] 專案根目錄：%ROOT%
echo [INFO] 後端路徑：%BACKEND_DIR%
echo [INFO] 前端路徑：%FRONTEND_DIR%
echo [INFO] Log 位置：%LOG_DIR%

if not exist "%BACKEND_DIR%" (
  echo [ERROR] 找不到後端路徑：%BACKEND_DIR%
  goto :EOF
)
if not exist "%FRONTEND_DIR%" (
  echo [ERROR] 找不到前端路徑：%FRONTEND_DIR%
  goto :EOF
)

echo [INFO] 檢查與移除既有前後端程序...
call app_clear.bat || echo [WARN] app_clear.bat 執行失敗 (errorlevel=%errorlevel%)



REM 重要：到專案根啟動，才能用 backend.main:app
echo [INFO] 啟動後端服務 (port 8080) ...
cd %ROOT%
start /b python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080

REM 前端建議不要用 80（需要系統管理員），先用 3000 測試
echo [INFO] 啟動前端開發伺服器 (port 3000) ...
cd %FRONTEND_DIR%
npm start

echo [INFO] 完成啟動流程；若需確認狀態，請查看 logs\backend_run.log / frontend_run.log。
endlocal
