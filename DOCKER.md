# Docker Setup Guide

This guide explains how to run the Trading Bot using Docker and Docker Compose.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- 4GB RAM minimum (8GB recommended)
- 5GB free disk space

## Quick Start

### 1. Configure Environment

```bash
# Copy the example env file
cp backend/.env.example backend/.env

# Edit .env and add your Zerodha API credentials
# API_KEY="your_kite_api_key"
# API_SECRET="your_kite_api_secret"
```

### 2. Build and Run (Development)

```bash
# Build and start all services
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

The services will be available at:
- Frontend: http://localhost
- Backend API: http://localhost:8000
- API Documentation: http://localhost:8000/docs

### 3. Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (clears all data)
docker-compose down -v
```

## Production Deployment

### Build for Production

```bash
# Build and start with production config
docker-compose -f docker-compose.prod.yml up -d --build
```

### Monitor Services

```bash
# View logs
docker-compose logs -f

# View logs for specific service
docker-compose logs -f backend
docker-compose logs -f frontend

# Check service status
docker-compose ps

# Check resource usage
docker stats
```

## Docker Commands Reference

### Container Management

```bash
# Restart a service
docker-compose restart backend

# Stop a specific service
docker-compose stop frontend

# Start a specific service
docker-compose start frontend

# Rebuild a specific service
docker-compose build backend
docker-compose up -d backend
```

### Logs and Debugging

```bash
# Follow logs in real-time
docker-compose logs -f --tail=100

# Execute command in running container
docker-compose exec backend bash
docker-compose exec frontend sh

# View container details
docker inspect trading_bot_backend
```

### Data Management

```bash
# List volumes
docker volume ls

# Inspect volume
docker volume inspect trading_bot_backend_data

# Backup database
docker cp trading_bot_backend:/app/data/trading_data_all.db ./backup/

# Restore database
docker cp ./backup/trading_data_all.db trading_bot_backend:/app/data/
```

### Clean Up

```bash
# Remove stopped containers
docker-compose rm

# Remove all containers, networks, and volumes
docker-compose down -v

# Remove unused images
docker image prune -a

# Complete cleanup (use with caution)
docker system prune -a --volumes
```

## Volumes

The following volumes are mounted for data persistence:

### Backend
- `./backend/data` - Database files
- `./backend/logs` - Application logs
- `./backend/access_token.json` - Zerodha access token
- `./backend/strategy_params.json` - Strategy parameters

## Networking

Services communicate through a dedicated bridge network `trading_bot_network`.

### Port Mapping
- `80:80` - Frontend (nginx)
- `8000:8000` - Backend (FastAPI)

## Health Checks

Both services include health checks:

### Backend
- Endpoint: http://localhost:8000/health
- Interval: 30s
- Timeout: 10s
- Retries: 3

### Frontend
- Endpoint: http://localhost/
- Interval: 30s
- Timeout: 3s
- Retries: 3

## Resource Limits (Production)

### Backend
- CPU Limit: 2 cores
- Memory Limit: 2GB
- CPU Reservation: 1 core
- Memory Reservation: 1GB

### Frontend
- CPU Limit: 0.5 cores
- Memory Limit: 512MB
- CPU Reservation: 0.25 cores
- Memory Reservation: 256MB

## Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs backend

# Check if port is already in use
netstat -ano | findstr :8000  # Windows
lsof -i :8000                 # Linux/macOS

# Rebuild without cache
docker-compose build --no-cache
```

### Cannot connect to backend

```bash
# Verify backend is running
docker-compose ps

# Check backend health
docker-compose exec backend python -c "import requests; print(requests.get('http://localhost:8000/health').text)"

# Check network connectivity
docker-compose exec frontend wget -O- http://backend:8000/health
```

### Database issues

```bash
# Access backend container
docker-compose exec backend bash

# Check database files
ls -la /app/data/

# Check database integrity
python -c "from core.database import engine; print(engine.connect())"
```

### High resource usage

```bash
# Check resource consumption
docker stats

# Restart services
docker-compose restart

# Apply resource limits (use production config)
docker-compose -f docker-compose.prod.yml up -d
```

## Security Best Practices

1. **Never commit .env file** - Keep credentials secure
2. **Use secrets management** - For production, use Docker secrets or env variables from CI/CD
3. **Update base images** - Regularly update Python and Node.js base images
4. **Scan for vulnerabilities** - Use `docker scan` to check images
5. **Run as non-root** - Consider adding USER directive in Dockerfile for production

## CI/CD Integration

### Build in GitHub Actions

```yaml
- name: Build Docker images
  run: |
    docker-compose build
    docker tag trading_bot_backend:latest ghcr.io/${{ github.repository }}/backend:latest
    docker tag trading_bot_frontend:latest ghcr.io/${{ github.repository }}/frontend:latest
```

### Push to Registry

```yaml
- name: Push to GitHub Container Registry
  run: |
    echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
    docker push ghcr.io/${{ github.repository }}/backend:latest
    docker push ghcr.io/${{ github.repository }}/frontend:latest
```

## Updating the Application

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker-compose down
docker-compose up -d --build

# Or for production
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml up -d --build
```

## Support

For issues with Docker setup:
1. Check logs: `docker-compose logs`
2. Verify environment variables in `.env`
3. Ensure ports 80 and 8000 are available
4. Check Docker daemon is running
5. Review [Docker Documentation](https://docs.docker.com/)

---

**Happy Trading with Docker! 🐳📈**
