# Publish FusionSolar plant data to MQTT

Containerized service that reads Huawei FusionSolar plant data and publishes a
retained JSON payload to an MQTT broker.

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

## Container image

The public image is available on Docker Hub:

```bash
docker pull senzolink/fusionsolar-mqtt:1.0.1
```

`compose.yaml` uses this versioned image by default. A versioned tag
keeps deployments reproducible; update the image tag deliberately when
upgrading.

## Configuration

```bash
cp .env.example .env
```

Set the following variables in `.env`; never commit it.

For local Docker Compose, this file is read automatically when Compose starts.
In a Compose management UI, add the same variables in its stack environment
editor. The Compose file passes them directly to the container and does not
require an `env_file` entry or a physical `.env` file alongside the YAML.

| Variable | Description |
| --- | --- |
| `FUSIONSOLAR_USERNAME` | Huawei FusionSolar account username. |
| `FUSIONSOLAR_PASSWORD` | Huawei FusionSolar account password. |
| `FUSIONSOLAR_SUBDOMAIN` | Required FusionSolar regional hostname prefix from your login URL. |
| `FUSIONSOLAR_PLANT_NAME` | Optional exact plant name as shown in FusionSolar. Required only when the account has multiple plants. |
| `MQTT_HOST` | Required hostname or IP address of the MQTT broker, reachable from the container. |
| `MQTT_PORT` | MQTT broker port; the default is `1883`. |
| `MQTT_TOPIC` | Any non-empty MQTT topic receiving the retained plant payload. |
| `MQTT_QOS` | MQTT quality of service (`0`, `1`, or `2`); defaults to `0`. |
| `MQTT_USERNAME` | Optional MQTT broker username. |
| `MQTT_PASSWORD` | Optional MQTT broker password; requires `MQTT_USERNAME`. |
| `MQTT_TLS_ENABLED` | Enables TLS for the MQTT connection; defaults to `false`. |
| `HA_DISCOVERY_ENABLED` | Enables standard Home Assistant MQTT discovery; defaults to `true`. Set to `false` when another service publishes discovery configuration. |
| `POLL_INTERVAL` | Polling interval in seconds; the default is `180`. |

## MQTT authentication and TLS

For a broker that requires credentials, set:

```env
MQTT_USERNAME=bridge
MQTT_PASSWORD=replace-with-a-secret
```

For a broker using a publicly trusted TLS certificate, enable TLS and use its
TLS port (commonly `8883`):

```env
MQTT_PORT=8883
MQTT_TLS_ENABLED=true
```

## FusionSolar regional endpoint

Set `FUSIONSOLAR_SUBDOMAIN` to the hostname prefix shown in the FusionSolar
login URL for your account. For example, if the browser URL begins with
`https://region01eu5.fusionsolar.huawei.com`, set:

```env
FUSIONSOLAR_SUBDOMAIN=region01eu5
```

The client also accepts the full FusionSolar hostname without `https://`, but
the short prefix is clearer. Do not guess the endpoint from your country; use
the exact hostname to which FusionSolar directs your account.

Huawei currently documents common prefixes including `intl`, `eu5`, `au7`,
`au1`, `br1`, `jp5`, `la5`, `sg5`, `intlobt`, and the European clusters
`region01eu5` through `region05eu5`. Some accounts use `uni...` prefixes, such
as `uni001eu5`. Huawei's list can change, so the login URL is the source of
truth. This bridge only accepts FusionSolar hosts; partner domains outside
`*.fusionsolar.huawei.com` are not supported.

For Huawei's exact regional host list, see [Domain Name List of Management Systems](https://info.support.huawei.com/DpinfoAppDoc/pre_erp_slice_00/doc/owner/pv_ess/en/en-us_topic_0000002513471347.html).

## Selecting a FusionSolar plant

For the usual case of one FusionSolar plant on an account, leave
`FUSIONSOLAR_PLANT_NAME` empty. The bridge discovers and selects that plant
automatically after signing in.

If the same FusionSolar account has access to more than one plant, set
`FUSIONSOLAR_PLANT_NAME` to the exact name shown in the FusionSolar app or web
portal:

```env
FUSIONSOLAR_PLANT_NAME=Home
```

The selected name must be unique on that FusionSolar account.

## MQTT topic

`MQTT_TOPIC` can be any non-empty MQTT topic. For example:

```text
solar/fusionsolar/state
```

The payload is retained on this topic, so MQTT clients that subscribe later
receive the latest known values immediately.


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
payload is retained and uses `MQTT_QOS`, which defaults to QoS 0. This suits
periodic telemetry: a missed update is replaced by the next poll, while the
broker keeps the latest successfully received payload. Set `MQTT_QOS=1` when
the publisher should wait for a broker acknowledgement, or `MQTT_QOS=2` only
when exactly-once delivery between the bridge and broker is specifically
required.

FusionSolar cloud values commonly refresh only every few minutes. The default
`POLL_INTERVAL=180` therefore avoids unnecessary API calls while keeping the
published state reasonably fresh; lower it only when your account actually
returns newer data more often.

## Power fallback

`flow_solar_power` is the primary power source. If it is `None`, the service
uses `currentPower`. A `currentPower` value of `0.0` is valid. If neither value
is available, that cycle does not publish a payload or a new timestamp.

## Self-repair

After an exception, the current FusionSolar client is discarded, so the next
attempt creates a new client and login. After three consecutive failures the
service waits 300 seconds. The first successful cycle resets the failure
counter. `restart: unless-stopped` restarts the container if its process exits,
unless it was manually stopped. It complements, but does not replace, the
in-process FusionSolar API retry logic.

## Home Assistant

By default, the bridge publishes retained Home Assistant MQTT Discovery
configuration for five sensors: current power plus daily, monthly, yearly, and
cumulative energy. With the `.env.example` defaults, the configuration topics
are:

```text
homeassistant/sensor/fusionsolar/<sensor>/config
```

This is the standard Home Assistant discovery prefix. Connect Home Assistant to
the same broker, enable MQTT discovery, then start the bridge. Each discovered
sensor reads the retained JSON from `MQTT_TOPIC`, for example:

```text
fusionsolar/state
```

## Custom Home Assistant discovery

If your own system, Node-RED, or another service already publishes Home
Assistant discovery configuration, disable the bridge's discovery messages:

```env
HA_DISCOVERY_ENABLED=false
```

The bridge then only publishes its retained JSON payload to `MQTT_TOPIC`. Your
custom discovery configuration should use that value as its `state_topic` and
read the documented JSON keys, for example `value_json.power`.

When enabled, bridge-managed discovery always uses Home Assistant's standard
`homeassistant` prefix. The discovery configuration also sets the sensor
subscription QoS from `MQTT_QOS`.

## Verifying publishes from container logs

After `wait_for_publish()` completes, the container logs the exact payload
topic and JSON, for example:

```text
MQTT published to solar/fusionsolar/state: {"power":1.743,...}
```

For QoS 1 or 2, this means the broker has completed its publish acknowledgement.
For QoS 0, it confirms that the client sent the message, but MQTT has no broker
acknowledgement. Neither case proves that Home Assistant has processed it.

## Development build

```bash
docker compose -f compose.dev.yaml up --build -d
```

## Logs

```bash
docker compose -f compose.dev.yaml logs -f fusionsolar-mqtt
```

## Production deployment

Create and configure `.env`, then pull and start the versioned public image:

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

## MQTT network

The Compose files do not require a pre-existing Docker network. Set
`MQTT_HOST` to a DNS name or IP address that is reachable from the container,
for example:

```env
MQTT_HOST=mqtt.example.net
```

If your broker is a service in another Docker Compose project, attach this
service to the same external network with a local Compose override. For
example, create `compose.override.yaml` next to `compose.yaml`:

```yaml
services:
  fusionsolar-mqtt:
    networks:
      - mqtt

networks:
  mqtt:
    external: true
    name: mqtt
```

Set `MQTT_HOST` to the broker's Docker DNS name on that shared network.

## Local MQTT test broker

`compose.test.yaml` is a complete, standalone test stack. It starts the bridge
and an ephemeral Mosquitto broker named `mqtt` on the same network. The broker
has no persistence and deliberately allows anonymous connections, so use it
only for local testing, never for production.

Set the required FusionSolar variables in your local `.env` or stack
environment editor. `MQTT_HOST` is not required for this test stack because it
is fixed to its internal broker.

Start the test stack:

```bash
docker compose -f compose.test.yaml up -d
```

Use `compose.test.yaml` only as an isolated test stack. For production, use
`compose.yaml` and set `MQTT_HOST` to your real broker.

To stop and remove the test broker, run:

```bash
docker compose -f compose.test.yaml down
```

## Build locally

```bash
docker build \
  -t fusionsolar-mqtt:local \
  .
```

## License

This project is licensed under the [MIT License](LICENSE).
