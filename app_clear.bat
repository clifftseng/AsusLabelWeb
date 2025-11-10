@echo off
chcp 65001 >nul
echo [INFO] 正在檢查並釋放 80 與 8080 連接埠...
echo.

for %%P in (80 8080) do (
    echo [INFO] 搜尋使用連接埠 %%P 的程序...
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr :%%P ^| findstr LISTENING') do (
        echo [INFO] 偵測到 PID %%A 使用連接埠 %%P
        for /f "tokens=1,*" %%B in ('tasklist /FI "PID eq %%A" /FO TABLE /NH') do (
            echo     ├─ %%B %%C
        )
        echo [INFO] 嘗試終止 PID %%A ...
        taskkill /F /PID %%A >nul 2>&1 && (
            echo [OK] PID %%A 已終止。
        ) || (
            echo [WARN] 無法終止 PID %%A（可能是系統服務或權限不足）。
        )
        echo.
    )
)

echo [INFO] 完成檢查。

