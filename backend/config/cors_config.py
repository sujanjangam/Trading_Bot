import os

# CORS Configuration
# For production, set ALLOWED_ORIGINS environment variable
# Example: ALLOWED_ORIGINS=https://myapp.com,https://www.myapp.com

def get_cors_origins():
    """Get CORS allowed origins based on environment"""
    env_origins = os.getenv("ALLOWED_ORIGINS", "")
    
    if env_origins:
        return [origin.strip() for origin in env_origins.split(",")]
    
    # Development: allow all for easier testing
    # Production: set ALLOWED_ORIGINS environment variable
    return ["*"]

CORS_ORIGINS = get_cors_origins()
