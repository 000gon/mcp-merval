# CLAUDE.md

Guía para asistente Claude al colaborar en este repositorio.

## Qué es mcp-merval

Servidor MCP (Model Context Protocol) mínimo para conectar Claude Desktop o Cursor con brokers Matriz DMA (ROFEX/MERVAL) usando la librería `pyRofex`. Todo ocurre vía stdio; no hay wrapper HTTP ni componentes de despliegue.

- Sesiones multiusuario en memoria.
- Cotizaciones y actualizaciones de órdenes en tiempo real.
- Herramientas para dólar MEP, portfolio y gestión de órdenes.
- Auto-login opcional usando `broker_config.json`.

⚠️ Todo se ejecuta contra el entorno LIVE. Revisá cada orden antes de enviarla.

## Layout del repo

```
server.py            # Servidor FastMCP (stdio)
config.py            # Carga de variables de entorno y brokers
lib/
  market_helpers.py
  pyrofex_session.py
  session_registry.py
pyRofex-master/      # Copia vendorizada de pyRofex (MIT)
```

No existen directorios de despliegue ni pruebas automáticas en esta versión recortada.

## Setup rápido

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
cp broker_config.example.json broker_config.json
python server.py
```

Configurá tu asistente MCP apuntando a `python server.py` dentro del repo.

## Pautas al desarrollar

- Los strings de usuario/log deben permanecer en castellano rioplatense.
- Mantener dependencias mínimas (`requirements.txt`).
- Reusar `_safe_json` para respuestas MCP.
- Evitar reintroducir componentes eliminados (Docker, FastAPI, etc.).

## Seguridad

- `broker_config.json` stay privado; no subir credenciales.
- Se puede usar `${ENV_VAR}` en el campo `password`.
- No se guarda estado fuera de memoria (reiniciar limpia sesiones).

Listo para que Claude/Cursor lo use. Añadí contexto adicional sólo si es necesario para entender cambios.
