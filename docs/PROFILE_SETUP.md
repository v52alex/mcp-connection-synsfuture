# Guía para crear un perfil de conexión Docker remoto

Esta guía prepara un perfil llamado `docker-remote1` para conectar el MCP con
Docker Desktop o Docker Engine remoto mediante SSH.

El procedimiento debe realizarse antes de instalar el MCP en Codex. El MCP no
crea Docker contexts, claves, aliases SSH ni configuración del sistema.

## Arquitectura

```text
Codex
  -> MCP mcp-connection-synsfuture
  -> perfil docker-remote1
  -> Docker context docker-remote1
  -> alias SSH docker-remote1
  -> Docker Engine remoto
```

La IP, el usuario remoto y la clave privada no se almacenan en el código del
MCP. Se configuran localmente mediante `~/.ssh/config`, `ssh-agent` o Keychain.

## Requisitos

En el equipo local:

- Docker CLI.
- Cliente OpenSSH.
- `ssh-keygen`, `ssh-add` y `ssh`.
- `uv` y Python 3.12+ para probar el MCP.

En el equipo remoto:

- OpenSSH Server activo en el puerto TCP `22`.
- Docker Desktop o Docker Engine activo.
- El usuario remoto con permiso para ejecutar Docker.
- La clave pública instalada en `authorized_keys`.

No se utilizan los puertos Docker TCP `2375` ni `2376`.

## 1. Crear una clave dedicada

Ejecutar en macOS o Linux. Si el archivo ya existe, no lo sobrescribas:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh

ssh-keygen \
  -t ed25519 \
  -a 100 \
  -C "mcp-connection-synsfuture-docker-remote1" \
  -f ~/.ssh/mcp-connection-synsfuture_docker_remote1
```

Permisos:

```bash
chmod 600 ~/.ssh/mcp-connection-synsfuture_docker_remote1
chmod 644 ~/.ssh/mcp-connection-synsfuture_docker_remote1.pub
```

La clave privada permanece en el equipo local. Solo se instala la clave pública
en el servidor remoto.

## 2. Instalar la clave pública en Windows

Mostrar la clave pública local:

```bash
cat ~/.ssh/mcp-connection-synsfuture_docker_remote1.pub
```

Antes de elegir la ruta, comprobar si el usuario remoto pertenece al grupo
`Administrators`:

```powershell
whoami /groups | Select-String "Administrators"
```

Si no pertenece a `Administrators`, copiar la línea completa al archivo del
usuario remoto:

```text
C:\Users\<WINDOWS_USER>\.ssh\authorized_keys
```

No copiar nunca el archivo sin extensión, que es la clave privada.

Si el usuario remoto pertenece a `Administrators`, OpenSSH normalmente utiliza
la ruta global:

```text
C:\ProgramData\ssh\administrators_authorized_keys
```

En ese caso, agrega la clave pública a ese archivo en lugar de utilizar solo
`C:\Users\<WINDOWS_USER>\.ssh\authorized_keys`:

```powershell
$source = "$env:USERPROFILE\.ssh\authorized_keys"
$target = "C:\ProgramData\ssh\administrators_authorized_keys"

if (-not (Test-Path $target)) {
    Copy-Item $source $target
}
```

Si el archivo ya contiene otras claves, no lo sobrescribas; agrega únicamente
la nueva clave pública y conserva las existentes.

Restringir los permisos del archivo administrativo:

```powershell
icacls $target /inheritance:r
icacls $target /grant:r "*S-1-5-32-544:F" "SYSTEM:F"
```

Para un usuario que no es administrador, revisar y restringir los permisos del
archivo de usuario:

```powershell
icacls "$env:USERPROFILE\.ssh" /inheritance:r
icacls "$env:USERPROFILE\.ssh" /grant:r "${env:USERNAME}:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r
icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "${env:USERNAME}:F" "SYSTEM:F"
```

Validar la configuración y reiniciar el servicio:

```powershell
& "$env:WINDIR\System32\OpenSSH\sshd.exe" -t
Restart-Service sshd
```

Para confirmar qué archivo contiene la clave y comparar su huella:

```powershell
ssh-keygen -lf "$env:USERPROFILE\.ssh\authorized_keys"
ssh-keygen -lf "C:\ProgramData\ssh\administrators_authorized_keys"
```

La huella debe coincidir con la clave pública generada en el equipo local.

## 3. Crear el alias SSH

Editar en el equipo local:

```bash
nano ~/.ssh/config
```

Agregar:

```sshconfig
Host docker-remote1
    HostName <REMOTE_HOST_OR_IP>
    User <REMOTE_USER>
    Port 22
    IdentityFile ~/.ssh/mcp-connection-synsfuture_docker_remote1
    IdentitiesOnly yes
```

Proteger el archivo:

```bash
chmod 600 ~/.ssh/config
```

El MCP solo utilizará el alias `docker-remote1`; no recibirá IPs ni usuarios
desde las llamadas del modelo.

## 4. Probar la autenticación SSH

```bash
ssh docker-remote1 "whoami && hostname && docker version"
```

La prueba debe devolver el usuario remoto, el hostname y la versión de Docker.

## Passphrase de la clave privada

La passphrase solicitada por `ssh-keygen` protege la clave privada local. No es
la contraseña del usuario remoto y no se debe copiar al servidor.

Es correcto utilizar una passphrase. Para no introducirla en cada operación,
carga la clave en el agente SSH:

```bash
ssh-add ~/.ssh/mcp-connection-synsfuture_docker_remote1
ssh-add -l
```

En macOS puede integrarse con Keychain mediante una entrada equivalente en
`~/.ssh/config`:

```sshconfig
Host docker-remote1
    HostName <REMOTE_HOST_OR_IP>
    User <REMOTE_USER>
    IdentityFile ~/.ssh/mcp-connection-synsfuture_docker_remote1
    IdentitiesOnly yes
    AddKeysToAgent yes
    UseKeychain yes
```

No guardes la passphrase en `.env`, `profiles.toml`, Git, scripts ni argumentos
del MCP.

## Si SSH solicita la contraseña del usuario remoto

Cuando el comando solicita la contraseña del usuario remoto, normalmente la
autenticación por clave pública no se completó. Una configuración correcta con
la clave cargada no debería necesitar esa contraseña.

Diagnóstico explícito sin fallback a password:

```bash
ssh \
  -o PreferredAuthentications=publickey \
  -o PasswordAuthentication=no \
  -vvv \
  docker-remote1 "whoami && hostname"
```

Revisar especialmente:

1. Que la clave pública instalada sea exactamente la salida de
   `mcp-connection-synsfuture_docker_remote1.pub`.
2. Que esté en el `authorized_keys` del usuario correcto.
3. Que `User` en `~/.ssh/config` sea el usuario que posee ese `authorized_keys`.
4. Que `IdentityFile` apunte a la clave privada correcta.
5. Que `IdentitiesOnly yes` no esté apuntando a una clave equivocada.
6. Que los permisos de `.ssh` y `authorized_keys` sean restrictivos.
7. Que OpenSSH Server esté activo y permita autenticación por clave pública.
8. Que el usuario remoto pueda ejecutar `docker version` en una sesión SSH no
   interactiva.

No conviene desactivar la contraseña como primera medida. Primero corrige la
autenticación por clave y, cuando esté validada, puedes aplicar una política
remota que deshabilite `PasswordAuthentication` según los controles operativos
del servidor.

## 5. Cargar la clave en el agente

```bash
ssh-add ~/.ssh/mcp-connection-synsfuture_docker_remote1
ssh-add -l
```

Si aparece `Could not open a connection to your authentication agent`, inicia
uno en la sesión actual:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/mcp-connection-synsfuture_docker_remote1
```

Al invocar `connect_connection_profile`, el MCP realiza esta comprobación
automáticamente antes de contactar el Docker Engine. Usa `SSH_AUTH_SOCK` si
Codex lo heredó y, cuando no existe, intenta descubrir el agente mediante
`launchctl` en macOS, `XDG_RUNTIME_DIR` en Linux o el named pipe de OpenSSH
Agent en Windows. También valida que exista una identidad cargada, pero nunca
devuelve la clave privada ni la passphrase. Si falla, la respuesta incluye
`state: ssh_agent_unavailable` y los pasos recomendados para corregirlo.

## 6. Crear el Docker context

Si el contexto no existe:

```bash
docker context create docker-remote1 \
  --docker "host=ssh://docker-remote1"
```

Si ya existe, no lo recrees. Inspecciónalo:

```bash
docker context inspect docker-remote1
```

Validar sin cambiar el contexto global:

```bash
docker --context docker-remote1 version
docker --context docker-remote1 ps -a
```

## 7. Crear el perfil del nuevo MCP

Desde la raíz del proyecto:

```bash
cd /Users/washingtonchavezpluas/Documents/Codex/Project/mcp-connection-synsfuture
cp profiles.example.toml profiles.toml
```

El archivo debe contener:

```toml
[profiles.docker-remote1]
type = "docker-context"
docker_context = "docker-remote1"
ssh_profile = "docker-remote1"
enabled = true
capabilities = ["read"]
```

`profiles.toml` es local y está excluido de Git.

## 8. Probar mediante el cliente MCP

```bash
uv sync --dev
uv run client.py connect_connection_profile docker-remote1 \
  --profiles-file profiles.toml
```

Resultado esperado:

```json
{
  "profile_id": "docker-remote1",
  "state": "ready",
  "connected": true,
  "transport": "ssh"
}
```

El cliente solo valida. No crea contextos, no carga claves y no ejecuta
mutaciones Docker.

`profile_id` es el nombre del perfil de conexión preconfigurado. Para un perfil
Docker, normalmente coincide con el nombre del Docker context y apunta al alias
SSH correspondiente. Si se omite, el MCP devuelve un mensaje descriptivo y
remite a esta guía y al README.

## 9. Instalar el MCP en Codex

Después de que la prueba local devuelva `state: ready`:

Codex debe recibir también el socket del `ssh-agent` si la aplicación no hereda
automáticamente `SSH_AUTH_SOCK`. Obtener la ruta en la terminal donde la clave
funciona:

```bash
echo "$SSH_AUTH_SOCK"
```

```bash
codex mcp add mcp-connection-synsfuture \
  --env MCP_CONNECTION_PROFILES_FILE=/ruta/absoluta/Project/mcp-connection-synsfuture/profiles.toml \
  --env SSH_AUTH_SOCK=/ruta/obtenida/de/SSH_AUTH_SOCK \
  -- /Users/washingtonchavezpluas/Documents/Codex/Project/mcp-connection-synsfuture/.venv/bin/mcp-connection-synsfuture
```

En macOS, el valor suele parecerse a:

```text
/var/run/com.apple.launchd.XXXX/Listeners
```

La ruta puede cambiar después de cerrar sesión o reiniciar macOS. Si el MCP
devuelve `unavailable` aunque el cliente local devuelva `ready`, verifica
`codex mcp get mcp-connection-synsfuture` y vuelve a registrar el servidor con
el `SSH_AUTH_SOCK` actual.

Verificar el registro:

```bash
codex mcp list
codex mcp get mcp-connection-synsfuture
```

Después de instalarlo, abrir una conversación nueva y solicitar la validación
del perfil `docker-remote1`.

## Seguridad

- No guardar claves privadas en el proyecto.
- No guardar passphrases en archivos de configuración.
- No copiar claves privadas a Windows.
- No enviar IPs, usuarios o claves como argumentos MCP.
- No utilizar `docker context use` durante pruebas automatizadas.
- No ejecutar comandos arbitrarios a través del MCP.
- Revocar la clave pública en `authorized_keys` si la clave privada se pierde o
  se sospecha que fue expuesta.
