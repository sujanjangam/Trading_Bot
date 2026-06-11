#!/bin/bash

# Trading Bot Docker Quick Start Script

set -e

echo "🚀 Trading Bot Docker Setup"
echo "=============================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    echo "   Visit: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Check if .env file exists
if [ ! -f "backend/.env" ]; then
    echo "⚙️  Setting up environment variables..."
    if [ -f "backend/.env.example" ]; then
        cp backend/.env.example backend/.env
        echo "✅ Created backend/.env from template"
        echo ""
        echo "⚠️  IMPORTANT: Please edit backend/.env and add your Zerodha API credentials"
        echo "   API_KEY=\"your_kite_api_key\""
        echo "   API_SECRET=\"your_kite_api_secret\""
        echo ""
        read -p "Press Enter after you've updated the .env file..."
    else
        echo "❌ backend/.env.example not found"
        exit 1
    fi
else
    echo "✅ Environment file exists"
    echo ""
fi

# Ask for deployment mode
echo "Select deployment mode:"
echo "1) Development (docker-compose.yml)"
echo "2) Production (docker-compose.prod.yml)"
read -p "Enter choice [1-2]: " mode

case $mode in
    1)
        COMPOSE_FILE="docker-compose.yml"
        echo "📦 Building and starting in DEVELOPMENT mode..."
        ;;
    2)
        COMPOSE_FILE="docker-compose.prod.yml"
        echo "📦 Building and starting in PRODUCTION mode..."
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

echo ""

# Build and start services
echo "🔨 Building Docker images (this may take a few minutes)..."
docker-compose -f $COMPOSE_FILE build

echo ""
echo "🚀 Starting services..."
docker-compose -f $COMPOSE_FILE up -d

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 10

# Check service status
echo ""
echo "📊 Service Status:"
docker-compose -f $COMPOSE_FILE ps

echo ""
echo "✅ Trading Bot is now running!"
echo ""
echo "📍 Access Points:"
echo "   Frontend:    http://localhost"
echo "   Backend API: http://localhost:8000"
echo "   API Docs:    http://localhost:8000/docs"
echo ""
echo "📝 Useful Commands:"
echo "   View logs:           docker-compose -f $COMPOSE_FILE logs -f"
echo "   Stop services:       docker-compose -f $COMPOSE_FILE down"
echo "   Restart services:    docker-compose -f $COMPOSE_FILE restart"
echo "   Check status:        docker-compose -f $COMPOSE_FILE ps"
echo ""
echo "📚 For more information, see DOCKER.md"
echo ""
echo "Happy Trading! 📈🚀"
