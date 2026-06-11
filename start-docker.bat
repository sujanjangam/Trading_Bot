@echo off
REM Trading Bot Docker Quick Start Script for Windows

echo.
echo Trading Bot Docker Setup
echo ==============================
echo.

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed. Please install Docker Desktop first.
    echo Visit: https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)

REM Check if Docker Compose is installed
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose is not installed.
    exit /b 1
)

echo [OK] Docker and Docker Compose are installed
echo.

REM Check if .env file exists
if not exist "backend\.env" (
    echo Setting up environment variables...
    if exist "backend\.env.example" (
        copy "backend\.env.example" "backend\.env"
        echo [OK] Created backend\.env from template
        echo.
        echo [WARNING] IMPORTANT: Please edit backend\.env and add your Zerodha API credentials
        echo API_KEY="your_kite_api_key"
        echo API_SECRET="your_kite_api_secret"
        echo.
        pause
    ) else (
        echo [ERROR] backend\.env.example not found
        exit /b 1
    )
) else (
    echo [OK] Environment file exists
    echo.
)

REM Ask for deployment mode
echo Select deployment mode:
echo 1) Development (docker-compose.yml)
echo 2) Production (docker-compose.prod.yml)
set /p mode="Enter choice [1-2]: "

if "%mode%"=="1" (
    set COMPOSE_FILE=docker-compose.yml
    echo Building and starting in DEVELOPMENT mode...
) else if "%mode%"=="2" (
    set COMPOSE_FILE=docker-compose.prod.yml
    echo Building and starting in PRODUCTION mode...
) else (
    echo [ERROR] Invalid choice
    exit /b 1
)

echo.
echo Building Docker images (this may take a few minutes)...
docker-compose -f %COMPOSE_FILE% build

echo.
echo Starting services...
docker-compose -f %COMPOSE_FILE% up -d

echo.
echo Waiting for services to be healthy...
timeout /t 10 /nobreak >nul

REM Check service status
echo.
echo Service Status:
docker-compose -f %COMPOSE_FILE% ps

echo.
echo [OK] Trading Bot is now running!
echo.
echo Access Points:
echo    Frontend:    http://localhost
echo    Backend API: http://localhost:8000
echo    API Docs:    http://localhost:8000/docs
echo.
echo Useful Commands:
echo    View logs:           docker-compose -f %COMPOSE_FILE% logs -f
echo    Stop services:       docker-compose -f %COMPOSE_FILE% down
echo    Restart services:    docker-compose -f %COMPOSE_FILE% restart
echo    Check status:        docker-compose -f %COMPOSE_FILE% ps
echo.
echo For more information, see DOCKER.md
echo.
echo Happy Trading!
echo.
pause
