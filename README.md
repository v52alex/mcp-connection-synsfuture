# mcp-connection-synsfuture

MCP universal para validar y utilizar conexiones remotas previamente
configuradas por el administrador del equipo.

## Estado del proyecto

```yaml
project_workflow:
  type: new-development
  status: discovery
  methodology: scrum
  owner: Alexis
  last_reviewed: 2026-08-23
  rationale: "Nuevo MCP universal basado en contextos de conexión preconfigurados."
```

## Product Goal

Permitir que Codex descubra, inspeccione y valide conexiones remotas autorizadas
mediante contextos ya creados en el equipo, sin almacenar ni hardcodear IPs,
usuarios, endpoints o claves privadas dentro del MCP.

## Principios iniciales

- El administrador crea y mantiene previamente los contextos de conexión.
- El MCP solo descubre, inspecciona y valida contextos autorizados.
- El MCP no crea, modifica ni elimina contextos, claves, aliases SSH o políticas
  del sistema.
- Las claves privadas permanecen en el agente SSH, Keychain o configuración local
  del sistema; nunca se reciben como argumentos MCP.
- Las operaciones remotas se habilitan únicamente después de una validación
  exitosa del contexto seleccionado.
- No se aceptan comandos arbitrarios provenientes del modelo.
- La lista de contextos permitidos se controla mediante configuración local y no
  mediante una decisión libre del modelo.

## Alcance inicial

### Fase 1: descubrimiento y validación

- Recibir explícitamente el identificador del perfil solicitado por el usuario.
- Validar que el perfil exista y esté incluido en la allowlist local.
- Inspeccionar metadata sanitizada del contexto asociado.
- Validar que el contexto pertenezca a la allowlist local.
- Validar que el endpoint remoto use SSH u otro transporte aprobado.
- Ejecutar una prueba de conectividad de solo lectura.
- Devolver estados estructurados y acciones recomendadas.

### Fase 2: perfiles SSH autorizados

- Descubrir aliases SSH configurados, sin leer claves privadas.
- Validar perfiles SSH previamente aprobados.
- Separar perfiles Docker de conexiones SSH genéricas para VPS.

### Fase 3: operaciones Docker controladas

- Listar e inspeccionar imágenes y contenedores mediante el contexto validado.
- Mantener `dry-run` y confirmaciones explícitas para mutaciones.
- Aplicar ownership, límites de ruta y auditoría sin secretos.

### Fuera del alcance inicial

- Crear Docker contexts o modificar `~/.ssh/config`.
- Generar, copiar o registrar claves SSH.
- Aceptar IPs, usuarios o comandos remotos enviados por el modelo.
- Cambiar automáticamente a un destino no autorizado.
- Gestionar DNS, certificados TLS o infraestructura del dominio.

## Contextos y dominio del proyecto

El dominio base documental es `synsfuture.com`, conforme a
[PROJECT_DOMAIN_CONVENTIONS.md](../../PROJECT_DOMAIN_CONVENTIONS.md). Este MCP
es una herramienta de infraestructura y no necesita una URL pública durante la
fase inicial; cualquier endpoint web futuro deberá registrarse explícitamente.

## Backlog inicial

| Prioridad | Elemento | Estado |
| --- | --- | --- |
| P0 | Definir contrato de contexto y estados de validación | discovery |
| P0 | Recibir y validar un perfil solicitado por el usuario | discovery |
| P0 | Inspeccionar y sanitizar metadata del perfil | discovery |
| P0 | Configurar allowlist de perfiles autorizados | discovery |
| P1 | Validar perfiles SSH separados de Docker | pending |
| P1 | Exponer herramientas MCP de solo lectura | pending |
| P2 | Migrar operaciones Docker controladas | pending |

## Definition of Ready

- Contexto, transporte, límites de seguridad y resultado observable definidos.
- Criterios de aceptación verificables.
- No requiere crear ni modificar infraestructura remota para comenzar.

## Definition of Done inicial

- Contratos y modelos documentados.
- Pruebas unitarias para estados correctos y fallos.
- No se exponen secretos ni endpoints sensibles.
- Allowlist y política de selección verificadas.
- Quality gate local configurado.

## Primer incremento implementado

- Contratos `ConnectionProfile`, `ConnectionState` y
  `ConnectionValidationResult` definidos con Pydantic.
- Perfiles cargados desde TOML local; el archivo operativo `profiles.toml` queda
  fuera de Git y la plantilla está en `profiles.example.toml`.
- `connect_connection_profile(profile_id)` valida el perfil dentro del MCP.
- Se inspecciona el Docker context asociado y se acepta únicamente transporte
  SSH antes de comprobar el Docker Engine remoto.
- No se ejecutan mutaciones ni se exponen endpoints, usuarios o claves.
- Quality gate inicial: 3 pruebas, Ruff y mypy correctos.

### Validación del perfil `docker-remote1`

El 2026-08-23 se creó el Docker context local `docker-remote1` apuntando al
alias SSH preconfigurado y se validó mediante el cliente MCP:

- Docker Client `29.0.1` respondió correctamente.
- Docker Desktop remoto `4.83.0 (234302)` y Engine `29.6.2` respondieron.
- `docker ps -a` fue accesible en modo de solo lectura.
- `connect_connection_profile("docker-remote1")` devolvió `state: ready` y
  `connected: true`.
- El transporte validado fue `ssh`.
- No se crearon, iniciaron, detuvieron ni eliminaron contenedores.
- La herramienta está anotada como lectura externa e idempotente para que Codex
  pueda ejecutarla sin tratar la validación como una mutación.

## Cliente MCP de desarrollo

El cliente `client.py` inicia `main.py` por `stdio`, descubre las herramientas
del servidor y ejecuta llamadas de prueba sin registrar todavía el MCP en Codex.

Preparar el entorno:

```bash
uv sync --dev
```

Listar las herramientas expuestas:

```bash
uv run client.py list
```

Validar un perfil explícito usando la plantilla de ejemplo:

```bash
uv run client.py connect_connection_profile docker-remote1 \
  --profiles-file profiles.example.toml
```

Para una prueba real, copia `profiles.example.toml` a un archivo local
`profiles.toml`, verifica que el Docker context y el alias SSH ya existan y
ejecuta:

```bash
uv run client.py connect_connection_profile docker-remote1 \
  --profiles-file profiles.toml
```

El cliente no crea contextos, no carga claves y no ejecuta mutaciones remotas.

La configuración completa del perfil, incluyendo SSH, passphrase, claves y
diagnóstico de contraseñas remotas, está en
[docs/PROFILE_SETUP.md](docs/PROFILE_SETUP.md).

## Uso explícito de herramientas

Mencionar únicamente `mcp-connection-synsfuture` no ejecuta una operación. Debe
indicarse la herramienta y sus argumentos. Si no se indica ninguna herramienta,
Codex debe responder que no se seleccionó ninguna y mostrar un ejemplo como:

```text
mcp-connection-synsfuture.connect_connection_profile(profile_id="docker-remote1")
```

La herramienta disponible actualmente es:

```text
connect_connection_profile(profile_id?)
```

`profile_id` es el identificador del perfil de conexión preconfigurado. En un
perfil Docker representa el Docker context autorizado; por ejemplo,
`docker-remote1`. Si se omite, la herramienta devuelve una explicación del
parámetro faltante y referencia esta documentación.

Durante la validación, el MCP comprueba automáticamente que la sesión tenga un
agente SSH disponible y que exista al menos una identidad cargada. Primero usa
`SSH_AUTH_SOCK` si fue heredado por Codex; si no, intenta descubrirlo mediante
los mecanismos estándar de macOS (`launchctl`), Linux (`XDG_RUNTIME_DIR`) y
Windows (OpenSSH Agent). Si no puede hacerlo, devuelve
`state: ssh_agent_unavailable` con los pasos recomendados para iniciar el
agente o cargar la clave. El MCP no devuelve claves privadas, passphrases ni el
contenido de `authorized_keys`.

En una conversación real de Codex debe invocarse la herramienta MCP directamente.
No debe ejecutarse `client.py`, `docker` ni comandos SSH como sustituto; `client.py`
es únicamente el cliente de desarrollo local.

También puede solicitarse en lenguaje natural:

```text
Usa connect_connection_profile del MCP mcp-connection-synsfuture con el perfil
docker-remote1. No ejecutes operaciones mutables.
```

## Instalación temporal en Codex

Después de validar el cliente localmente, registrar el MCP con una ruta absoluta:

```bash
codex mcp add mcp-connection-synsfuture \
  --env MCP_CONNECTION_PROFILES_FILE=/ruta/absoluta/Project/mcp-connection-synsfuture/profiles.toml \
  -- /ruta/absoluta/Project/mcp-connection-synsfuture/.venv/bin/mcp-connection-synsfuture
```

La configuración de perfiles debe estar disponible mediante la variable local
`MCP_CONNECTION_PROFILES_FILE` en el entorno del servidor MCP. Esta instalación
es solo para pruebas y no reemplaza todavía `codex-connection`.

La lista de herramientas MCP se descubre al iniciar la sesión. Si el servidor se
registra o reinstala mientras una conversación ya está abierta, esa conversación
puede no mostrarlo y Codex podría recurrir al `client.py` de desarrollo. En ese
caso, iniciar una sesión nueva antes de probar el MCP.

## Instalación desde un repositorio

El proyecto está empaquetado como una aplicación Python y expone el comando
`mcp-connection-synsfuture`. Durante esta etapa el código se mantiene en `dev`:

```bash
uv tool install "git+https://github.com/v52alex/mcp-connection-synsfuture.git@dev"
```

La instalación no incluye perfiles reales ni secretos. Crea la configuración
local del equipo:

```bash
mkdir -p "$HOME/.config/mcp-connection-synsfuture"
cp profiles.example.toml \
  "$HOME/.config/mcp-connection-synsfuture/profiles.toml"
```

Registra el MCP en Codex usando esa configuración:

```bash
codex mcp add mcp-connection-synsfuture \
  --env MCP_CONNECTION_PROFILES_FILE="$HOME/.config/mcp-connection-synsfuture/profiles.toml" \
  -- mcp-connection-synsfuture
```

El usuario debe preparar sus propias claves, aliases SSH, Docker contexts y
perfiles autorizados. El MCP descubre el agente SSH durante la validación y no
requiere registrar manualmente `SSH_AUTH_SOCK`.

## Interacción esperada

Codex solo recopila la entrada del usuario y la envía al MCP. La existencia,
allowlist, configuración, transporte, autenticación y conectividad se validan
exclusivamente dentro del MCP.

Codex solicitará al usuario el identificador del perfil, por ejemplo:

```text
¿Qué perfil de conexión deseas utilizar?
```

El usuario responderá:

```text
docker-remote1
```

Después, Codex invocará el MCP y este ejecutará una validación equivalente a:

```text
connect_connection_profile(profile_id="docker-remote1")
  -> existe el perfil
  -> está permitido por la allowlist
  -> existe el Docker context asociado
  -> el transporte es válido
  -> la conexión responde
  -> devuelve ready y sus capacidades
```

El MCP no debe listar perfiles automáticamente ni seleccionar uno por
proximidad, nombre parecido o disponibilidad. Si el perfil no existe o no está
autorizado, la conexión termina sin intentar otro destino.

## Próximo incremento

Ampliar la cobertura de estados y agregar perfiles SSH para VPS sin mezclar su
contrato con los perfiles Docker.
