# ADR 0001: Contextos de conexión preconfigurados

## Estado

Aceptada para discovery.

## Contexto

El MCP anterior estaba acoplado al nombre `windows-docker` y a una configuración
específica. El nuevo MCP debe funcionar con destinos remotos preparados por el
administrador, evitando IPs, usuarios, endpoints y claves dentro del código o de
las llamadas del modelo.

## Decisión

El MCP exigirá que el contexto de conexión exista antes de iniciar cualquier
operación. El flujo será:

```text
recibir profile_id -> comprobar existencia -> comprobar allowlist ->
inspeccionar -> validar conectividad -> operar
```

El MCP no creará ni modificará Docker contexts, aliases SSH, claves, agentes o
configuración del sistema.

La selección se limitará a nombres incluidos en una allowlist local. El nombre
del contexto podrá solicitarse como entrada, pero nunca permitirá saltarse la
allowlist ni ejecutar un destino arbitrario.

La capa conversacional puede solicitar al usuario el identificador del perfil,
por ejemplo `docker-remote1`, pero solo el MCP valida su existencia, allowlist,
configuración, transporte, autenticación y conectividad. El MCP no listará
perfiles automáticamente ni probará otros perfiles si el solicitado no existe,
no está permitido o no está disponible.

Docker y SSH se modelarán como tipos de conexión distintos:

- `docker-context`: operaciones sobre un Docker Engine mediante un contexto
  preconfigurado.
- `ssh-profile`: validación o acceso controlado a un VPS mediante un alias SSH
  preconfigurado.

## Consecuencias

### Positivas

- El MCP no contiene datos de infraestructura específicos.
- Las claves permanecen bajo control del sistema operativo y del administrador.
- Se reduce el riesgo de conexiones arbitrarias o de exponer endpoints.
- El mismo contrato puede reutilizarse para Windows, Linux y otros destinos.

### Costes y límites

- La preparación del contexto queda fuera del MCP.
- Un contexto existente puede estar mal configurado; por eso la inspección y la
  prueba de conectividad son obligatorias.
- La allowlist debe mantenerse cuando se agreguen o retiren destinos.
- No se debe confundir la existencia del contexto con una conexión disponible.

## Rechazado

- Permitir que el modelo envíe IP, usuario o clave para crear una conexión.
- Crear contextos automáticamente durante una operación MCP.
- Usar un único perfil universal para mezclar Docker remoto y VPS genérico.
