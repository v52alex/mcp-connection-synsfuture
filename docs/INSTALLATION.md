# Instalación de mcp-connection-synsfuture

## Requisitos

- Python 3.12 o superior.
- `uv` instalado.
- Codex instalado.
- OpenSSH y Docker CLI para perfiles Docker remotos.

## Instalación desde Git

Reemplaza `OWNER/REPO` por el repositorio oficial:

```bash
uv tool install "git+https://github.com/OWNER/REPO.git"
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
