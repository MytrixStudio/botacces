# Mitryx Discord Control

Bot de Discord + API HTTP para controlar solicitudes de acceso del mod `menuMitryx`.

## Requisitos

- Python 3.11 o superior.
- Bot creado en Discord Developer Portal con Application ID `1530734359957471317`.
- Los cinco canales configurados y visibles para el bot.
- Un dominio con HTTPS para producción. El mod 2.1 viene preconfigurado para `https://botacces.onrender.com`.
- RCON opcional para ejecutar comandos en Minecraft.

## 1. Crear el archivo `.env`

```bash
cp .env.example .env
```

En Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edita `.env` y coloca:

```dotenv
DISCORD_TOKEN=TOKEN_REAL_DEL_BOT
SERVER_API_KEY=UNA_CLAVE_LARGA_Y_ALEATORIA
```

Genera una clave segura con:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Los IDs solicitados ya están escritos en `.env.example`.

## 2. Instalar y ejecutar con Python

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

La API escucha por defecto en `0.0.0.0:8080`. Comprueba:

```text
GET http://127.0.0.1:8080/health
```

## 3. Ejecutar con Docker

```bash
docker compose up -d --build
```

El compose publica la API solo en `127.0.0.1:8080`, para colocar Nginx delante. La base de datos queda en `./data/mitryx.sqlite3`.

## 4. Invitar el bot

Usa los scopes:

- `bot`
- `applications.commands`

Permisos mínimos recomendados:

- View Channels
- Send Messages
- Embed Links
- Read Message History

URL preparada para esta aplicación:

```text
https://discord.com/oauth2/authorize?client_id=1530734359957471317&permissions=84992&scope=bot%20applications.commands
```

Los botones administrativos solo aceptan miembros con `Administrator` o `Manage Server`.

## 5. Publicar por HTTPS

Hay un ejemplo en:

```text
deploy/nginx-mitryx.conf.example
```

Reemplaza `acceso.example.com`, instala un certificado válido y configura en `.env`:

```dotenv
TRUST_PROXY_HEADERS=true
```

Actívalo únicamente cuando la API esté detrás de un proxy que tú controles. Dejarlo activo con acceso directo permitiría falsificar `X-Forwarded-For`.

## 6. Configurar RCON y whitelist

En el servidor Minecraft, usa como base `deploy/server.properties.example`:

```properties
online-mode=true
white-list=true
enforce-whitelist=true
enable-rcon=true
rcon.port=25575
rcon.password=UNA_PASSWORD_RCON_DIFERENTE
```

En `.env`:

```dotenv
RCON_HOST=127.0.0.1
RCON_PORT=25575
RCON_PASSWORD=UNA_PASSWORD_RCON_DIFERENTE
RCON_SYNC_WHITELIST=true
```

Cuando la sincronización está activa:

- Aprobar ejecuta `whitelist add <jugador>`.
- Rechazar o banear ejecuta `whitelist remove <jugador>`.
- Banear ejecuta además `ban-ip <ip> <motivo>` cuando hay una IP válida y `kick <jugador> <motivo>` para expulsarlo si ya está conectado.
- Desbanear ejecuta `pardon-ip <ip>` y vuelve a agregar a whitelist.

Si RCON falla, el estado en SQLite/API sigue bloqueando el siguiente acceso verificado por el mod del servidor.

## Paneles y flujo

### Canal de aprobación

Cada solicitud crea un embed con:

- nombre de Minecraft;
- UUID;
- IP detectada;
- botones **Aprobar** y **Rechazar**.

### Canal de aprobados

Cada aprobación crea una tarjeta con botón **Banear IP**. Cuando el jugador entra al servidor, la tarjeta se actualiza con la IP observada por el servidor.

### Canal de desbaneo

Cada baneo crea una tarjeta con motivo y botón **Desbanear**.

### Canal de IP del servidor

Panel persistente con botones **Agregar**, **Editar** y **Remover**. Los clientes consultan el cambio cada 10 segundos y solo reciben la dirección si están aprobados.

## Comandos slash

- `/actualizar_paneles`: reconstruye paneles y tarjetas faltantes.
- `/estado_mytrix`: muestra los contadores y la dirección actual.

## Base de datos

SQLite usa modo WAL y guarda:

- UUID y nombre de Minecraft;
- estado;
- IP de solicitud, última IP del cliente e IP observada por el servidor;
- administradores que aprobaron, banearon o desbanearon;
- motivo y fechas;
- IDs de mensajes persistentes;
- registro de auditoría.

Haz copias periódicas de `data/mitryx.sqlite3` y de sus archivos WAL/SHM con el proceso detenido o usando una herramienta compatible con SQLite.


## Flujo automático del cliente 2.1

El jugador no escribe ningún ID ni abre un formulario. Al pulsar **SOLICITAR JUGAR** o **JUGAR** sin aprobación, el mod envía automáticamente a la API:

- UUID de Minecraft;
- nick de Minecraft;
- IP pública observada por Render.

La API publica inmediatamente el panel con **Aprobar** y **Rechazar** en Discord. Para que Render entregue la IP real del jugador, usa `TRUST_PROXY_HEADERS=true`.
