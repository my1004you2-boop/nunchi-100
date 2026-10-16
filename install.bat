@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   시간 알림 설치
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python을 찾을 수 없어요.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요.
    echo 설치할 때 "Add python.exe to PATH"를 꼭 체크하세요.
    pause
    exit /b 1
)

echo 필요한 패키지를 설치하고 있어요...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [오류] 패키지 설치에 실패했어요. 인터넷 연결을 확인하고 다시 실행하세요.
    pause
    exit /b 1
)

echo.
echo 설치가 끝났어요. 시간 알림을 실행할게요.
echo - 창을 닫아도 작업 표시줄 오른쪽 트레이에서 계속 실행돼요.
echo - 트레이 아이콘을 더블클릭하면 창이 다시 열려요.
echo - PC를 켤 때 자동으로 실행하려면 [설정] 탭에서 켜세요.
start "" pythonw "%~dp0time_alarm.pyw"
echo.
pause
