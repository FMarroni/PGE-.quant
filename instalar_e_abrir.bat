@echo off
chcp 65001 >nul
title Painel de Jurimetria - PGE/SP
setlocal enabledelayedexpansion

set "DIR=%~dp0"
set "PYTHON_DIR=%DIR%python_local"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PY_VER=3.11.9"
set "PY_ZIP=python-%PY_VER%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_ZIP%"

echo.
echo  =============================================
echo   Painel Estrategico de Jurimetria - PGE/SP
echo  =============================================
echo.

if exist "%PYTHON_EXE%" goto :executar

echo  [CONFIGURACAO INICIAL - apenas na primeira vez]
echo  Baixando e configurando Python portatil...
echo  Aguarde, isso pode levar alguns minutos.
echo.

if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

echo  [1/4] Baixando Python %PY_VER%...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PYTHON_DIR%\%PY_ZIP%' -UseBasicParsing"
if errorlevel 1 goto :erro_download

echo  [2/4] Extraindo Python...
powershell -NoProfile -Command "Expand-Archive -Path '%PYTHON_DIR%\%PY_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
del "%PYTHON_DIR%\%PY_ZIP%" >nul 2>&1

echo  [3/4] Configurando ambiente...
powershell -NoProfile -Command "$f = (Get-ChildItem '%PYTHON_DIR%\python*._pth').FullName; (Get-Content $f -Raw) -replace '#import site','import site' | Set-Content $f -NoNewline"

powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYTHON_DIR%\get-pip.py' -UseBasicParsing"
"%PYTHON_EXE%" "%PYTHON_DIR%\get-pip.py" --quiet
del "%PYTHON_DIR%\get-pip.py" >nul 2>&1

echo  [4/4] Instalando dependencias...
"%PYTHON_EXE%" -m pip install streamlit pandas plotly psycopg2-binary --quiet --disable-pip-version-check
if errorlevel 1 goto :erro_deps

echo.
echo  [OK] Configuracao concluida!
echo.
goto :executar

:erro_download
echo.
echo  [ERRO] Falha ao baixar Python.
echo  Verifique sua conexao com a internet e tente novamente.
echo.
pause
exit /b 1

:erro_deps
echo.
echo  [ERRO] Falha ao instalar dependencias.
echo  Verifique sua conexao com a internet e tente novamente.
echo.
pause
exit /b 1

:executar
echo  Iniciando o painel no navegador...
echo  (Aguarde alguns segundos)
echo.
echo  Para encerrar: feche esta janela ou pressione CTRL+C
echo.

start "" /b cmd /c "timeout /t 4 >nul && start http://localhost:8501"

"%PYTHON_EXE%" -m streamlit run "%DIR%dashboard_jurimetria.py" --server.headless true --browser.gatherUsageStats false --server.port 8501

pause
