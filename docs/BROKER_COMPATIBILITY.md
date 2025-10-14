# Broker Compatibility Guide

This guide explains how to expand mcp-merval to support additional brokers beyond ECO and VETA.

## Overview

mcp-merval is built on top of pyRofex, which supports any broker using the ROFEX/Matba protocol. The broker configuration system makes it easy to add new brokers without modifying code.

## Supported Brokers (Out of the Box)

- **ECO Valores** (`eco`) - `https://api.eco.xoms.com.ar/`
- **VETA Capital** (`veta`) - `https://api.veta.xoms.com.ar/`

## Adding a New Broker

### Step 1: Update Broker Configuration

Edit your `broker_config.json` (copy from `broker_config.example.json` if needed):

```json
{
  "brokers": {
    "eco": {
      "name": "Eco Valores",
      "api_url": "https://api.eco.xoms.com.ar/",
      "environment": "LIVE",
      "timeout_seconds": 10,
      "default": true
    },
    "your_broker": {
      "name": "Your Broker Name",
      "api_url": "https://api.yourbroker.com.ar/",
      "environment": "LIVE",
      "timeout_seconds": 10,
      "default": false,
      "description": "Custom broker configuration"
    }
  }
}
```

### Step 2: Add User Account Configuration (Auto-Login)

In the same `broker_config.json`, configure your account with credentials for auto-login:

```json
{
  "user_accounts": {
    "your_user_id": {
      "broker": "your_broker",
      "username": "YOUR_ROFEX_USERNAME",
      "password": "${YOUR_BROKER_PASSWORD}",
      "account": "YOUR_ACCOUNT_NUMBER"
    }
  }
}
```

**Security Note**: Use environment variables for passwords: `"password": "${ENV_VAR_NAME}"`

### Step 3: Test Connection

Create a small verification script (outside the repository if you prefer) to confirm the configuration:

```python
from config import settings

broker = settings.get_broker_config("your_broker")
assert broker is not None
assert broker["api_url"] == "https://api.yourbroker.com.ar/"
```

For a full end-to-end validation you must log in with real credentials and fetch market data using the MCP tools.

### Step 4: Use Your Broker

**Option A: Auto-Login (Recommended)**

If you configured credentials in `broker_config.json`, just start using tools:

```python
# No login needed! Just call any tool with your user_id
get_market_data(user_id="your_user_id", symbol="GGAL")
```

The server will authenticate automatically on first use.

**Option B: Manual Login**

You can still manually authenticate at runtime:

```python
# Manual login with explicit credentials
login(
    user_id="your_user_id",
    user="YOUR_USERNAME",
    password="YOUR_PASSWORD",
    account="YOUR_ACCOUNT",
    environment="LIVE"
)
```

## Known Compatible Brokers

Brokers using the ROFEX/Matba protocol should work out of the box:

### Confirmed Working
- ✅ ECO Valores
- ✅ VETA Capital

### Potentially Compatible (Untested)
- 🔄 **Invertir Online (IOL)** - May require API URL discovery
- 🔄 **Bull Market Brokers** - May require API URL discovery
- 🔄 **Cohen** - May require API URL discovery
- 🔄 **Portfolio Personal** - May require API URL discovery

> **Note**: If you successfully test any of these brokers, please contribute back by:
> 1. Adding the broker configuration example to `broker_config.example.json`
> 2. Updating this documentation
> 3. Submitting a pull request

## Broker-Specific Considerations

### API URL Discovery

Most brokers using ROFEX will have an API URL in the format:
```
https://api.{broker}.xoms.com.ar/
```

To find your broker's API URL:
1. Contact your broker's support
2. Check their API documentation
3. Look for "ROFEX API" or "DMA API" in their developer resources

### Connection Settings

Different brokers may have different:
- **Timeout requirements**: Adjust `timeout_seconds` if experiencing disconnections
- **Rate limits**: Monitor for rate limiting errors and adjust request frequency
- **Market data access**: Some brokers may have restricted symbol access

### Symbol Availability

Different brokers may offer different instruments:
- **Core MERVAL**: Most brokers support major stocks and bonds
- **Options/Futures**: Availability varies by broker
- **MEP instruments**: AL30/AL30D typically available, verify others

## Troubleshooting

### Connection Issues

**Problem**: Cannot connect to broker API
```
Error: Connection timeout / Invalid API URL
```

**Solutions**:
1. Verify API URL with your broker
2. Check internet connectivity
3. Verify broker credentials are correct
4. Increase `timeout_seconds` in broker config

### Authentication Issues

**Problem**: Login fails with valid credentials
```
Error: Authentication failed
```

**Solutions**:
1. Verify credentials with broker's web platform first
2. Check if API access is enabled for your account
3. Verify environment is "LIVE" (not "REMARKET")
4. Contact broker support to enable API access

### Symbol Not Found

**Problem**: Cannot get market data for specific symbols
```
Error: Symbol not found / Invalid instrument
```

**Solutions**:
1. Verify symbol is available through your broker
2. Check symbol format (e.g., "AL30" vs "AL30/DIC4")
3. Use `get_market_data` to test symbol availability
4. Consult broker's instrument list

## Advanced Configuration

### Custom Validation

If your broker requires special validation, extend `config.py`:

```python
def validate_broker_specific(self, broker_id: str) -> None:
    """Add broker-specific validation logic."""
    broker = self.get_broker_config(broker_id)
    if not broker:
        return

    # Custom validation for specific broker
    if broker_id == "your_broker":
        # Add specific checks
        pass
```

### Environment-Specific Settings

Some brokers may have separate LIVE/REMARKET URLs:

```json
{
  "brokers": {
    "your_broker": {
      "name": "Your Broker",
      "environments": {
        "LIVE": {
          "api_url": "https://api.live.yourbroker.com.ar/",
          "timeout_seconds": 10
        },
        "REMARKET": {
          "api_url": "https://api.remarket.yourbroker.com.ar/",
          "timeout_seconds": 15
        }
      }
    }
  }
}
```

> **Note**: Current implementation focuses on LIVE only. REMARKET support would require code changes in `config.py` and `server.py`.

## Contributing Broker Support

If you add support for a new broker, please contribute:

1. **Update `broker_config.example.json`** with your broker's configuration
2. **Test thoroughly** with real credentials
3. **Document any quirks** or special considerations
4. **Submit a PR** with:
   - Broker configuration example
   - Updated documentation
   - Test results

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

## Security Notes

- **Never commit** `broker_config.json` (it's gitignored)
- ⚠️ **Storing passwords in JSON is INSECURE** for production use
- ✅ **Recommended**: Use environment variables with `"password": "${ENV_VAR_NAME}"` syntax
- ✅ **Alternative**: Use manual `login()` tool with runtime credentials
- **Sanitize logs** before sharing for debugging
- **File permissions**: Ensure `broker_config.json` has restricted permissions (chmod 600)

### Best Practices

1. **Development**: Store passwords in environment variables, reference them in config
2. **Production**: Use secrets management (AWS Secrets Manager, HashiCorp Vault, etc.)
3. **CI/CD**: Never store credentials in config files, inject at runtime
4. **Multi-user**: Each user should have separate credentials, never share accounts

## Resources

- [pyRofex Documentation](https://github.com/matbarofex/pyRofex)
- [ROFEX API Documentation](https://apihub.primary.com.ar/)
- [Model Context Protocol (MCP) Spec](https://modelcontextprotocol.io/)

## Need Help?

- Open an issue on GitHub with `[broker-support]` tag
- Include broker name and any error messages (sanitized)
- Check existing issues for similar problems
