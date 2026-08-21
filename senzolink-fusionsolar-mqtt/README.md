# SenzoLink FusionSolar → MQTT bridge

This container reads plant data from Huawei FusionSolar and publishes it to the
local MQTT broker. The retained MQTT payload is available to SenzoLink flows in
Node-RED and to Home Assistant, which receives MQTT Discovery configurations on
startup.

## Architecture

```text
Huawei FusionSolar
        |
        v
fusionsolar-mqtt
        |
        v
local Mosquitto
        |
        +--> Node-RED
        |
        +--> Home Assistant
```

## MQTT payload

```json
{
  "power": 1.964,
  "daily_energy": 3.83,
  "month_energy": 285.44,
  "year_energy": 2864.13,
  "cumulative_energy": 14755.45,
  "timestamp": 1787120000
}
```

All values are examples. `power` is published in kW and energy values in kWh.

## Power fallback

`flow_solar_power` is the primary power measurement. If it is `None`, the
bridge falls back to `currentPower`. A `currentPower` value of `0.0` is a valid
zero-production measurement. If neither value is available, the bridge raises
an error and does not publish a new payload or timestamp.

## Self-repair

After every exception the bridge discards its FusionSolar client, so the next
attempt creates a new login/session. Three consecutive failures cause a 300 s
backoff; a successful cycle resets the failure counter. Docker
`restart: unless-stopped` only restarts the container if its process actually
ends—the Python application does not restart Docker or systemd itself.

## Local FusionSolar package

The working `fusionsolar_api` package from the existing SenzoLink installation
is included unchanged. Its required external dependencies are listed in
`requirements.txt`.

## Configuration

```bash
cp .env.example .env
```

Fill in the FusionSolar account, plant ID, and SenzoLink MQTT identifiers in
`.env`. Do not commit this file.

## Development build

```bash
docker compose -f docker-compose.dev.yml build
docker compose -f docker-compose.dev.yml up -d
```

The development container is named `fusionsolar-mqtt-dev`, so it can coexist
with the production Compose definition. Do not start it on a gateway while the
existing systemd service publishes to the same MQTT topic.

## Logs

```bash
docker logs -f fusionsolar-mqtt-dev
```

Use `fusionsolar-mqtt` when running the production Compose service.

## Production deployment

Build and push the image first (see [Registry](#registry)), then on the gateway:

```bash
docker compose pull
docker compose up -d
```

Check status:

```bash
docker compose ps
```

Restart the service:

```bash
docker compose restart fusionsolar-mqtt
```

Stop it:

```bash
docker compose down
```

## Docker network

The external Docker network `proxy` must already exist:

```bash
docker network inspect proxy
```

The container needs this network to resolve and reach the local MQTT broker.

## MQTT DNS

`MQTT_HOST` must not be `127.0.0.1`, because that address points back to the
bridge container. Use the MQTT container's Docker DNS name, for example:

```env
MQTT_HOST=mqtt
```

## Registry

The production Compose file uses the private registry image
`registry.kalamiri.dev/senzolink-fusionsolar-mqtt:1.0.0`.

Authenticate before building or pulling if the registry requires it:

```bash
docker login registry.kalamiri.dev
```

```bash
docker build \
  -t registry.kalamiri.dev/senzolink-fusionsolar-mqtt:1.0.0 \
  .
```

```bash
docker push \
  registry.kalamiri.dev/senzolink-fusionsolar-mqtt:1.0.0
```

Optionally also publish `latest`:

```bash
docker tag registry.kalamiri.dev/senzolink-fusionsolar-mqtt:1.0.0 \
  registry.kalamiri.dev/senzolink-fusionsolar-mqtt:latest
docker push registry.kalamiri.dev/senzolink-fusionsolar-mqtt:latest
```

## Git

```bash
git init
git add .
git commit -m "Initial FusionSolar MQTT Docker bridge"
```

No remote is configured or pushed automatically.
