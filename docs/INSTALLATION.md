# Instalación de mcp-connection-synsfuture

## Requisitos

- Python 3.12 o superior.
- `uv` instalado.
- Codex instalado.
- OpenSSH y Docker CLI para perfiles Docker remotos.

## Instalación desde Git

Durante esta etapa el código se mantiene en la rama `dev`:

```bash
uv tool install "git+https://github.com/v52alex/mcp-connection-synsfuture.git@dev"
```

## Configuración local

Los perfiles son propios de cada equipo. No se descargan del repositorio ni se
versionan:

```bash
mkdir -p "$HOME/.config/mcp-connection-synsfuture"
cp profiles.example.toml \
  "$HOME/.config/mcp-connection-synsfuture/profiles.toml"
```

Edita `profiles.toml` con los contextos previamente configurados. No agregues
claves privadas, passphrases ni secretos.

## Registro en Codex

```bash
codex mcp add mcp-connection-synsfuture \
  --env MCP_CONNECTION_PROFILES_FILE="$HOME/.config/mcp-connection-synsfuture/profiles.toml" \
  -- mcp-connection-synsfuture
```

No es necesario configurar `SSH_AUTH_SOCK`. Durante la validación, el MCP
intenta usar la variable heredada y descubrir el agente SSH según la plataforma.

## Validación

Inicia una nueva sesión de Codex y ejecuta:

```text
mcp-connection-synsfuture.connect_connection_profile(
  profile_id="docker-remote1"
)
```

Si falta configuración, el MCP devuelve un estado estructurado y una acción
recomendada. Consulta [PROFILE_SETUP.md](PROFILE_SETUP.md) para preparar claves,
aliases SSH y Docker contexts.

## Prueba real contra Windows

Si el Docker Engine remoto está en Windows y el contexto local se llama
`windows-docker`, registra ese contexto en el MCP y úsalo como `profile_id`:

```text
mcp-connection-synsfuture.register_connection_profile(
  profile_id="windows-docker",
  docker_context="windows-docker",
  ssh_profile="windows-docker",
  capabilities=["read"]
)

mcp-connection-synsfuture.connect_connection_profile(
  profile_id="windows-docker"
)

mcp-connection-synsfuture.list_containers_docker(
  profile_id="windows-docker"
)
```

El perfil enlaza tres identificadores locales: el `profile_id` que recibe el
MCP, el nombre del Docker context y el alias SSH. El MCP valida el contexto y
ejecuta las herramientas Docker mediante `docker --context windows-docker`.
No es necesario proporcionar la IP o el usuario en cada llamada.

Para confirmar la preparación fuera del MCP, sin cambiar el contexto Docker
activo, puede usarse:

```bash
docker context inspect windows-docker
docker --context windows-docker version
docker --context windows-docker ps -a
```

Después de esta comprobación, las operaciones reales deben ejecutarse mediante
las herramientas MCP y su `profile_id="windows-docker"`.

## Registrar un perfil mediante el MCP

Cuando el perfil no exista, proporciona los datos al MCP; no es necesario editar
el archivo manualmente:

```text
mcp-connection-synsfuture.register_connection_profile(
  profile_id="vps",
  docker_context="vps",
  ssh_profile="vps",
  capabilities=["read"]
)
```

El MCP registra el bloque equivalente:

```toml
[profiles.vps]
type = "docker-context"
docker_context = "vps"
ssh_profile = "vps"
enabled = true
capabilities = ["read"]
```

El archivo se crea automáticamente en la ruta de configuración de la
plataforma. El registro no crea el alias SSH ni el Docker context; después de
prepararlos, ejecuta `connect_connection_profile` para validarlos.

Para una conexión VPS sin Docker, registra un perfil SSH:

```text
mcp-connection-synsfuture.register_connection_profile(
  profile_id="vps",
  profile_type="ssh-profile",
  ssh_profile="vps",
  capabilities=["read"]
)
```

El alias `vps` debe estar configurado previamente en SSH. El MCP validará el
alias mediante una sesión no interactiva con autenticación por clave.

## Eliminar un perfil

Para eliminar solamente el registro local:

```text
mcp-connection-synsfuture.remove_connection_profile(
  profile_id="vps"
)
```

El MCP no elimina el Docker context, el alias SSH, las claves ni recursos
remotos. Si el perfil no existe, devuelve `profile_not_found`.

## Listar perfiles

Para consultar los perfiles registrados en el archivo local:

```text
mcp-connection-synsfuture.list_connection_profiles()
```

El resultado es sanitizado y no incluye claves privadas, passphrases ni
credenciales. Todas las herramientas incluyen también un campo
`documentation_hint` que indica consultar la documentación del MCP.

Las herramientas Docker de solo lectura requieren siempre el perfil explícito:

```text
mcp-connection-synsfuture.list_images_docker(profile_id="docker-remote1")
mcp-connection-synsfuture.list_containers_docker(profile_id="docker-remote1")
mcp-connection-synsfuture.container_logs_docker(
  profile_id="docker-remote1",
  container_name="mi-contenedor",
  tail=100
)
```

Las herramientas mutables requieren confirmación explícita. La creación se
planifica por defecto:

```text
mcp-connection-synsfuture.create_container_docker(
  profile_id="docker-remote1",
  image_reference="nginx:latest",
  container_name="web"
)
```

Para ejecutar se debe indicar `dry_run=false` y
`confirmation="CONFIRM_CREATE"`. Iniciar, detener, reiniciar y eliminar usan
respectivamente `CONFIRM_START`, `CONFIRM_STOP`, `CONFIRM_RESTART` y
`CONFIRM_RM`. Solo se permiten operaciones sobre contenedores gestionados por
el MCP.

Compose también requiere un perfil explícito para las consultas de solo lectura:

```text
mcp-connection-synsfuture.inspect_compose_project_docker(
  profile_id="docker-remote1",
  project_path="/ruta/al/proyecto"
)

mcp-connection-synsfuture.compose_ps_docker(
  profile_id="docker-remote1",
  project_path="/ruta/al/proyecto",
  env_file="/ruta/segura/Project/.env"
)
```

`env_file` es opcional. Úsalo cuando el archivo de variables no esté junto al
Compose; el MCP solo pasa la ruta a Docker y nunca lee ni devuelve su contenido.
Los resultados no incluyen valores de entorno ni credenciales del proyecto.

Las operaciones Compose mutables requieren confirmación explícita:

```text
mcp-connection-synsfuture.compose_up_docker(
  profile_id="docker-remote1",
  project_path="/ruta/al/proyecto"
)
```

Usa `CONFIRM_COMPOSE_UP`, `CONFIRM_COMPOSE_STOP`,
`CONFIRM_COMPOSE_RESTART`, `CONFIRM_COMPOSE_DOWN`,
`CONFIRM_COMPOSE_DEPLOY` o `CONFIRM_COMPOSE_REMOVE`, según la operación.
`plan_compose_deployment_docker` y `audit_compose_project_docker` no mutan el
entorno.

También están disponibles la inspección local y el build de imágenes:

```text
mcp-connection-synsfuture.inspect_docker_project_docker(
  project_path="/ruta/al/proyecto"
)

mcp-connection-synsfuture.build_image_docker(
  profile_id="docker-remote1",
  project_path="/ruta/al/proyecto",
  image_name="mi-imagen"
)
```

El build se planifica por defecto y requiere `CONFIRM_BUILD` con
`dry_run=false` para ejecutarse. `list_audit_events_docker` devuelve eventos
sanitizados cuando exista un almacén de auditoría configurado.
