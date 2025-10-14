# mcp-merval 🇦🇷

Servidor Model Context Protocol (MCP) que conecta Claude Desktop, Cursor o cualquier asistente compatible con MCP al mercado MERVAL de Argentina a través de brokers compatibles con Matriz DMA (Ecovalores, Veta, etc).

> ⚠️ Todos los comandos hablan con la API de Matriz en producción. Probá con montos chicos y revisá las órdenes antes de autorizarlas.

---

## Funcionalidades
- **Sesiones multiusuario** en memoria con reautenticación automática.
- **Datos de mercado en tiempo real** vía WebSockets de Matriz (cotizaciones, estados de órdenes, cartera).
- **Herramientas para el disparo de órdenes** para enviar, cancelar e inspeccionar órdenes en vivo.
- **Calculadora de MEP** con vistas previas para flujos AL30/AL30D.
- **Ejecución MEP MARKET** con herramientas dedicadas que ejecutan ambas piernas desde la previsualización.
- **Configuración externa del broker** usando un JSON simple y secretos opcionales por variables de entorno.

---

## Inicio Rápido

```bash
git clone https://github.com/000gon/mcp-merval.git
cd mcp-merval

python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Editá `broker_config.json` con tus credenciales de Matriz DMA. El archivo incluye ejemplos de configuración para brokers Eco Valores y Veta Capital.

Levantá el servidor MCP por stdio:

```bash
python server.py
```

Tu asistente ahora puede conectarse por stdio (Claude Desktop, Cursor, cliente FastMCP propio, etc.).

---

## Configuración

### Variables de Entorno (`.env`)

| Variable | Propósito | Predeterminado |
| --- | --- | --- |
| `LOG_LEVEL` | Verbosidad de logs | `INFO` |
| `SESSION_TTL_HOURS` | Ventana de expiración de sesión | `8` |
| `COMMISSION_RATE` | Comisión por defecto aplicada en helpers | `0.005` |
| `FORCE_LIVE_ENVIRONMENT` | Evita el uso de REMARKET | `true` |
| `USE_PYROFEX_FOR_MEP` | Habilita helpers de MEP basados en pyRofex | `true` |
| `PYROFEX_TIMEOUT_SECONDS` | Timeout de REST en segundos | `10` |
| `BROKER_CONFIG_PATH` | Ruta al JSON de configuración de brokers | `broker_config.json` |

### Configuración de Broker (`broker_config.json`)

```json
{
  "brokers": {
    "eco": {
      "name": "Eco Valores",
      "api_url": "https://api.eco.xoms.com.ar/",
      "timeout_seconds": 10,
      "default": true
    },
    "veta": {
      "name": "Veta Capital",
      "api_url": "https://api.veta.xoms.com.ar/",
      "timeout_seconds": 10,
      "default": false
    }
  },
  "user_accounts": {
    "trader": {
      "broker": "eco",
      "username": "(tu CUIT sin guiones)",
      "password": "(tu contraseña de Matriz DMA)",
      "account": "(tu número de cuenta comitente)"
    }
  }
}
```

- Reemplazá los valores entre paréntesis con tus credenciales reales
- Podés usar `${ENV_VAR}` en el campo de contraseña para cargar secretos desde variables de entorno
- La primera entrada en `user_accounts` se usa para el inicio de sesión automático cuando se invoca una herramienta
- **Importante**: No subas tus credenciales reales a repositorios públicos

---

## Integración con Claude Desktop / Cursor

Agregá el servidor a Claude Desktop creando o editando `claude_desktop_config.json`:

Agregá la ruta de donde guardaste el MCP Server

```json
{
  "servers": [
    {
      "name": "mcp-merval",
      "command": "python",
      "args": ["server.py"],
      "cwd": "/absolute/path/to/mcp-merval"
    }
  ]
}
```

Reiniciá el cliente y las herramientas de `mcp-merval` van a aparecer en el listado. En Cursor, apuntá la configuración MCP al mismo comando.

---

## Estructura del Repositorio

```
config.py               # Cargador de configuración de entorno y brokers
lib/
  tools/                # Paquete con cada grupo de herramientas MCP
    __init__.py         # Helper para registrar todas las herramientas
    auth.py             # Login, estado de sesión, logout, healthchecks
    market_data.py      # Cotizaciones, instrumentos, búsquedas
    trading.py          # Órdenes, posiciones y movimientos
    mep.py              # Flujos de dólar MEP
    websocket.py        # Suscripciones en tiempo real y caché
    common.py           # Utilidades compartidas (JSON, sesiones, helpers)
  market_helpers.py     # Utilidades de símbolos, detección de bonos, normalización de precios
  pyrofex_session.py    # Capa liviana alrededor de sesiones de pyRofex
  session_registry.py   # Registro en memoria de sesiones y suscripciones
pyRofex-master/         # pyRofex
server.py               # Bootstrap del servidor FastMCP
tests/                  # Pruebas unitarias (pytest)
```


---

## Notas de Desarrollo

- Ejecutá `python server.py` durante el desarrollo; las herramientas se recargan solo cuando el proceso se reinicia.
- Linters/formatters opcionales (Black, Ruff, etc.) no están incluidos: usá tu configuración preferida.
- Hay un conjunto mínimo de tests (`pytest`) para validar los flujos críticos; corrélos antes de desplegar cambios relevantes.

---

## Licencia y Descargo

Publicado bajo la [Licencia MIT](LICENSE). La librería `pyRofex` incluida mantiene su propia licencia MIT en `pyRofex-master/`.

Operar, y más con herramientas que automatizan la operatoria, es riesgoso. Este software se proporciona **tal cual** para fomentar la innovación en el mercado de capitales argentino. Verificá credenciales, parámetros de órdenes y respuestas del broker antes de aceptar los llamados al MCP.

Hecho con ❤️ para la comunidad financiera argentina 🇦🇷
