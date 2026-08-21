# SenzoLink FusionSolar → MQTT bridge

Docker service that reads Huawei FusionSolar plant data and publishes it to an
MQTT broker. It also publishes Home Assistant MQTT Discovery configurations.

## Architecture

```text
Huawei FusionSolar
        |
        v
fusionsolar-mqtt
        |
        v
MQTT
```

## Requirements

- Docker and Docker Compose
- An MQTT broker reachable from the container
- A Huawei FusionSolar account
- A FusionSolar plant ID

## Configuration

```bash
cp .env.example .env
```

Set the following variables in `.env`; never commit it.

| Variable | Description |
| --- | --- |
| `FUSIONSOLAR_USERNAME` | Huawei FusionSolar account username. |
| `FUSIONSOLAR_PASSWORD` | Huawei FusionSolar account password. |
| `FUSIONSOLAR_SUBDOMAIN` | FusionSolar regional subdomain; the default is `uni001eu5`. |
| `FUSIONSOLAR_PLANT_ID` | Plant identifier to query. |
| `MQTT_HOST` | Docker DNS name or hostname of the MQTT broker, such as `mqtt`. |
| `MQTT_PORT` | MQTT broker port; the default is `1883`. |
| `MQTT_TOPIC` | Required SenzoLink-compatible topic receiving the retained plant payload. |
| `POLL_INTERVAL` | Polling interval in seconds; the default is `60`. |

## MQTT topic and SenzoLink convention

`MQTT_TOPIC` is not an arbitrary MQTT topic in this version of the service. It
must use this exact four-part format:

```text
client/CLIENT_ID/GATEWAY_ID/FusionSolar
```

The service uses `CLIENT_ID` and `GATEWAY_ID` to build stable Home Assistant
device identifiers and discovery topics. For a non-SenzoLink installation,
choose your own stable values, for example:

```text
client/my-home/gateway-1/FusionSolar
```

Do not use a topic such as `solar/fusionsolar`: the application rejects it
because it cannot derive the required identifiers.

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

Values are illustrative. Power is in kW and energy values are in kWh. The
payload is published with QoS 1 and retained.

## Power fallback

`flow_solar_power` is the primary power source. If it is `None`, the service
uses `currentPower`. A `currentPower` value of `0.0` is valid. If neither value
is available, that cycle does not publish a payload or a new timestamp.

## Self-repair

After an exception, the current FusionSolar client is discarded, so the next
attempt creates a new client and login. After three consecutive failures the
service waits 300 seconds. The first successful cycle resets the failure
counter. `restart: unless-stopped` only protects against a stopped process; it
does not replace API retry logic.

## Home Assistant discovery

On startup the service publishes retained MQTT Discovery sensors for current
power and daily, monthly, yearly, and cumulative production.

For `MQTT_TOPIC=client/CLIENT_ID/GATEWAY_ID/FusionSolar`, the configuration
messages are published below:

```text
client/CLIENT_ID/ha/sensor/fusionsolar/<sensor>/config
```

Home Assistant uses `homeassistant` as its default MQTT discovery prefix, so
change the MQTT integration's discovery prefix to this matching value:

```text
client/CLIENT_ID/ha
```

In Home Assistant, open **Settings → Devices & services → MQTT → Configure →
Configure MQTT Options**, then set the discovery prefix. The broker and client
ID must match the values used by this service. After starting the container,
look for the **FusionSolar** device in Home Assistant. The retained discovery
messages also let Home Assistant discover the sensors after a restart.

See the [Home Assistant MQTT documentation](https://www.home-assistant.io/integrations/mqtt/)
for its current MQTT integration setup and discovery options.

## Development build

```bash
docker compose -f docker-compose.dev.yml build
docker compose -f docker-compose.dev.yml up -d
```

## Logs

```bash
docker logs -f fusionsolar-mqtt-dev
```

## Production deployment

Replace `REGISTRY_HOST` in `docker-compose.yml` with your private registry
hostname, then:

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

## Docker network

The Compose examples use an external Docker network named `proxy`. The broker
is reached through Docker DNS, for example:

```env
MQTT_HOST=mqtt
```

Users with a different Docker environment can adapt the Compose network and
broker hostname accordingly.

## Registry build

```bash
docker build \
  -t REGISTRY_HOST/senzolink/fusionsolar-mqtt:1.0.0 \
  .
```

```bash
docker push \
  REGISTRY_HOST/senzolink/fusionsolar-mqtt:1.0.0
```
