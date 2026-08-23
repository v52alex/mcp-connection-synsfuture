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
