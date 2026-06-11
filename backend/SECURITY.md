# Security Guidelines

## Critical Security Rules

### 1. Never Commit Sensitive Data
The following files contain sensitive information and are in `.gitignore`:
- `.env` - API keys and secrets
- `access_token.json` - Kite access tokens
- `*.db` - Trading databases with personal data

### 2. Environment Variables
All sensitive configuration must be in `.env`:
```bash
API_KEY=your_kite_api_key
API_SECRET=your_kite_api_secret
```

### 3. Access Token Management
- Access tokens are valid for ONE trading day only
- Tokens are automatically saved to `access_token.json`
- Never hardcode tokens in source code
- The system auto-loads tokens on startup

### 4. Production Deployment
Before deploying to production:
1. Set `ALLOWED_ORIGINS` environment variable with your frontend domain
2. Never use `allow_origins=["*"]` in production
3. Use HTTPS for all API communication
4. Regularly rotate API keys

### 5. Code Review Checklist
Before committing code, verify:
- [ ] No hardcoded tokens or credentials
- [ ] No sensitive data in logs
- [ ] `.env` file is in `.gitignore`
- [ ] No API keys in comments or documentation

## Incident Response
If credentials are accidentally committed:
1. Immediately revoke the API key at Kite Connect dashboard
2. Generate new API credentials
3. Update `.env` with new credentials
4. Remove sensitive data from git history using `git filter-branch`
