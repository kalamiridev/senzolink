import os
import json
import time
import logging
import math
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from fusionsolar_api.client import FusionSolarClient


# --------------------------------------------------
# Environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

def required_env(name):
    value = os.getenv(name, "")
    if not value.strip():
        raise RuntimeError(f"{name} is required")
    return value


def env_bool(name, default):
    return os.getenv(name, default).strip().lower() not in {
    "0",
    "false",
    "no",
    "off"
}

def env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, default).strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc

    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")

    return value


def optional_env_time(name):
    value = os.getenv(name, "").strip()

    if not value:
        return None

    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise RuntimeError(f"{name} must use HH:MM format") from exc


FUSIONSOLAR_USERNAME = required_env("FUSIONSOLAR_USERNAME").strip()
FUSIONSOLAR_PASSWORD = required_env("FUSIONSOLAR_PASSWORD")
FUSIONSOLAR_SUBDOMAIN = required_env("FUSIONSOLAR_SUBDOMAIN").strip()
FUSIONSOLAR_PLANT_NAME = os.getenv("FUSIONSOLAR_PLANT_NAME", "").strip()

MQTT_HOST = required_env("MQTT_HOST").strip()
MQTT_PORT = env_int("MQTT_PORT", "1883", 1, 65535)
MQTT_TOPIC = required_env("MQTT_TOPIC").strip()
MQTT_QOS = env_int("MQTT_QOS", "0", 0, 2)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TLS_ENABLED = env_bool("MQTT_TLS_ENABLED", "false")
MQTT_CONNECT_TIMEOUT = env_int("MQTT_CONNECT_TIMEOUT", "30", 1, 300)
MQTT_PUBLISH_TIMEOUT = env_int("MQTT_PUBLISH_TIMEOUT", "30", 1, 300)

HA_DISCOVERY_ENABLED = env_bool("HA_DISCOVERY_ENABLED", "true")
HA_DISCOVERY_NODE_ID = os.getenv("HA_DISCOVERY_NODE_ID", "fusionsolar").strip()

DAY_POLL_INTERVAL = env_int("POLL_INTERVAL", "180", 30, 86400)
FUSIONSOLAR_PAUSE_START = optional_env_time("FUSIONSOLAR_PAUSE_START")
FUSIONSOLAR_PAUSE_END = optional_env_time("FUSIONSOLAR_PAUSE_END")
FUSIONSOLAR_PAUSE_TIMEZONE_NAME = os.getenv("TZ", "").strip()

MAX_CONSECUTIVE_FAILURES = 3
FAILURE_BACKOFF = 300
SESSION_REFRESH_INTERVAL = 25 * 60
FUSIONSOLAR_PAUSE_TIMEZONE = None

if any(character in MQTT_TOPIC for character in ("+", "#", "\x00")):
    raise RuntimeError("MQTT_TOPIC must be a publishable MQTT topic without + or #")

if not HA_DISCOVERY_NODE_ID or any(
    character in HA_DISCOVERY_NODE_ID for character in ("/", "+", "#", "\x00")
):
    raise RuntimeError("HA_DISCOVERY_NODE_ID must not contain /, +, or #")

if MQTT_PASSWORD and not MQTT_USERNAME:
    raise RuntimeError(
        "MQTT_USERNAME is required when MQTT_PASSWORD is set"
    )

if (FUSIONSOLAR_PAUSE_START is None) != (FUSIONSOLAR_PAUSE_END is None):
    raise RuntimeError(
        "Set both FUSIONSOLAR_PAUSE_START and FUSIONSOLAR_PAUSE_END, or neither"
    )

if FUSIONSOLAR_PAUSE_START is not None:
    if not FUSIONSOLAR_PAUSE_TIMEZONE_NAME:
        raise RuntimeError("TZ is required when FusionSolar pause times are set")

    if FUSIONSOLAR_PAUSE_START == FUSIONSOLAR_PAUSE_END:
        raise RuntimeError("FUSIONSOLAR_PAUSE_START and FUSIONSOLAR_PAUSE_END must differ")

    try:
        FUSIONSOLAR_PAUSE_TIMEZONE = ZoneInfo(FUSIONSOLAR_PAUSE_TIMEZONE_NAME)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "TZ is not a valid IANA time zone: "
            f"{FUSIONSOLAR_PAUSE_TIMEZONE_NAME}"
        ) from exc

HA_DEVICE_ID = HA_DISCOVERY_NODE_ID
DISCOVERY_BASE = f"homeassistant/sensor/{HA_DISCOVERY_NODE_ID}"

# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


# --------------------------------------------------
# FusionSolar client
# --------------------------------------------------

def create_fusionsolar_client():

    logging.info(
        "Connecting to FusionSolar..."
    )

    return FusionSolarClient(
        username=FUSIONSOLAR_USERNAME,
        password=FUSIONSOLAR_PASSWORD,
        huawei_subdomain=FUSIONSOLAR_SUBDOMAIN
    )


def get_plant_name(plant):

    for key in (
        "stationName",
        "name",
        "station_name"
    ):

        value = plant.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def describe_plants(plants):

    return ", ".join(
        get_plant_name(plant) or plant.get("dn", "<unknown>")
        for plant in plants
    )


def select_plant_id(client):

    plants = client.get_station_list()

    if not plants:
        raise RuntimeError(
            "No FusionSolar plants are available to this account"
        )

    if FUSIONSOLAR_PLANT_NAME:

        matches = [
            plant
            for plant in plants
            if (
                get_plant_name(plant) or ""
            ).casefold() == FUSIONSOLAR_PLANT_NAME.casefold()
        ]

        if len(matches) == 1:

            plant_id = matches[0].get("dn")

            if not plant_id:
                raise RuntimeError(
                    "FusionSolar returned a plant without an ID"
                )

            logging.info(
                "Selected FusionSolar plant: %s",
                get_plant_name(matches[0])
            )

            return plant_id

        if len(matches) > 1:
            raise RuntimeError(
                "More than one FusionSolar plant matches "
                f"FUSIONSOLAR_PLANT_NAME={FUSIONSOLAR_PLANT_NAME!r}; "
                "use a unique name in FusionSolar"
            )

        raise RuntimeError(
            "No FusionSolar plant matches "
            f"FUSIONSOLAR_PLANT_NAME={FUSIONSOLAR_PLANT_NAME!r}. "
            f"Available plants: {describe_plants(plants)}"
        )

    if len(plants) == 1:

        plant_id = plants[0].get("dn")

        if not plant_id:
            raise RuntimeError(
                "FusionSolar returned a plant without an ID"
            )

        logging.info(
            "Automatically selected FusionSolar plant: %s",
            get_plant_name(plants[0]) or plant_id
        )

        return plant_id

    raise RuntimeError(
        "This FusionSolar account has multiple plants. Set "
        "FUSIONSOLAR_PLANT_NAME to the exact name shown in "
        f"FusionSolar. Available plants: {describe_plants(plants)}"
    )


# --------------------------------------------------
# Home Assistant MQTT Discovery
# --------------------------------------------------

def publish_discovery(client):

    device = {
        "identifiers": [HA_DEVICE_ID],
        "name": "FusionSolar",
        "manufacturer": "Huawei",
        "model": "FusionSolar Plant"
    }

    sensors = {

        "power": {
            "name": "Current Power",
            "unique_id": f"{HA_DEVICE_ID}_power",
            "device_class": "power",
            "state_class": "measurement",
            "unit_of_measurement": "kW",
            "value_template": "{{ value_json.power }}"
        },

        "daily_energy": {
            "name": "Daily Production",
            "unique_id": f"{HA_DEVICE_ID}_daily_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.daily_energy }}"
        },

        "month_energy": {
            "name": "Monthly Production",
            "unique_id": f"{HA_DEVICE_ID}_month_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.month_energy }}"
        },

        "year_energy": {
            "name": "Yearly Production",
            "unique_id": f"{HA_DEVICE_ID}_year_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.year_energy }}"
        },

        "cumulative_energy": {
            "name": "Total Production",
            "unique_id": f"{HA_DEVICE_ID}_cumulative_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.cumulative_energy }}"
        }
    }

    for sensor_id, config in sensors.items():

        config["state_topic"] = MQTT_TOPIC
        config["qos"] = MQTT_QOS
        config["device"] = device

        discovery_topic = (
            f"{DISCOVERY_BASE}/{sensor_id}/config"
        )

        publish_and_wait(
            client,
            discovery_topic,
            json.dumps(config),
            qos=MQTT_QOS,
            retain=True,
        )

    logging.info(
        "Home Assistant discovery published under %s",
        DISCOVERY_BASE
    )


# --------------------------------------------------
# MQTT client
# --------------------------------------------------

def publish_and_wait(client, topic, payload, qos, retain):
    result = client.publish(topic, payload, qos=qos, retain=retain)

    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"MQTT publish failed with code {result.rc}")

    deadline = time.monotonic() + MQTT_PUBLISH_TIMEOUT

    while not result.is_published():
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"MQTT publish to {topic} did not complete within "
                f"{MQTT_PUBLISH_TIMEOUT} seconds"
            )
        time.sleep(0.1)


def create_mqtt_client():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )
    connected = threading.Event()
    connection_error = []

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            connected.set()
            return
        connection_error.append(f"broker rejected connection: {reason_code}")
        connected.set()

    def on_connect_fail(_client, _userdata):
        logging.warning("MQTT connection attempt failed; retrying")

    def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties):
        if reason_code != 0:
            logging.warning("MQTT disconnected unexpectedly: %s", reason_code)

    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect

    if MQTT_USERNAME:
        client.username_pw_set(
            MQTT_USERNAME,
            MQTT_PASSWORD or None
        )

    if MQTT_TLS_ENABLED:
        client.tls_set()

    client.connect_async(
        MQTT_HOST,
        MQTT_PORT,
        60
    )
    client.loop_start()

    if not connected.wait(MQTT_CONNECT_TIMEOUT):
        client.loop_stop()
        raise RuntimeError(
            f"MQTT connection to {MQTT_HOST}:{MQTT_PORT} timed out after "
            f"{MQTT_CONNECT_TIMEOUT} seconds"
        )

    if connection_error:
        client.loop_stop()
        raise RuntimeError(f"MQTT connection failed: {connection_error[-1]}")

    if HA_DISCOVERY_ENABLED:
        publish_discovery(client)

    return client


# --------------------------------------------------
# Main
# --------------------------------------------------

def numeric_value(value):
    if value in (None, "", "-", "--", "N/A", "n/a"):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def is_time_in_range(current_time, start_time, end_time):
    if start_time < end_time:
        return start_time <= current_time < end_time

    return current_time >= start_time or current_time < end_time


def fusionsolar_pause_seconds():
    if FUSIONSOLAR_PAUSE_START is None:
        return 0

    now = datetime.now(FUSIONSOLAR_PAUSE_TIMEZONE)

    if not is_time_in_range(
        now.time(), FUSIONSOLAR_PAUSE_START, FUSIONSOLAR_PAUSE_END
    ):
        return 0

    end_at = datetime.combine(
        now.date(),
        FUSIONSOLAR_PAUSE_END,
        tzinfo=FUSIONSOLAR_PAUSE_TIMEZONE,
    )

    if end_at <= now:
        end_at += timedelta(days=1)

    return max(1, math.ceil((end_at - now).total_seconds()))


def main():

    mqtt_client = create_mqtt_client()

    fusion_client = None
    fusion_client_created_at = None

    plant_id = None

    consecutive_failures = 0
    while True:

        pause_seconds = fusionsolar_pause_seconds()

        if pause_seconds:
            logging.info(
                "FusionSolar pause active; next poll in %d minutes",
                math.ceil(pause_seconds / 60),
            )
            time.sleep(pause_seconds)
            continue

        try:

            # --------------------------------------
            # Create/recreate FusionSolar session
            # --------------------------------------

            if (
                fusion_client is not None
                and fusion_client_created_at is not None
                and (
                    time.monotonic() - fusion_client_created_at
                ) >= SESSION_REFRESH_INTERVAL
            ):

                logging.info(
                    "Refreshing FusionSolar session proactively"
                )

                fusion_client = None
                fusion_client_created_at = None
                plant_id = None

            if fusion_client is None:

                fusion_client = (
                    create_fusionsolar_client()
                )

                fusion_client_created_at = time.monotonic()

                plant_id = select_plant_id(
                    fusion_client
                )

            # --------------------------------------
            # Get plant data
            # --------------------------------------

            data = (
                fusion_client
                .get_current_plant_data(
                    plant_id
                )
            )

            if not isinstance(data, dict):

                raise RuntimeError(
                    "FusionSolar returned invalid response"
                )

            # --------------------------------------
            # Solar power
            # --------------------------------------

            flow_power = numeric_value(data.get("flow_solar_power"))
            current_power = numeric_value(data.get("currentPower"))

            if flow_power is not None:

                power = flow_power

            elif current_power is not None:

                power = current_power

                logging.warning(
                    "flow_solar_power unavailable; "
                    "using currentPower: %s kW",
                    current_power
                )

            else:

                raise RuntimeError(
                    "FusionSolar did not return a numeric solar power value "
                    f"(available keys: {', '.join(sorted(data))})"
                )

            # --------------------------------------
            # Energy values
            # --------------------------------------

            daily_energy = numeric_value(data.get("dailyEnergy"))
            month_energy = numeric_value(data.get("monthEnergy"))
            year_energy = numeric_value(data.get("yearEnergy"))
            cumulative_energy = numeric_value(data.get("cumulativeEnergy"))

            # --------------------------------------
            # Build MQTT payload
            # --------------------------------------

            payload = {

                "power": power,
                "daily_energy": daily_energy,
                "month_energy": month_energy,
                "year_energy": year_energy,
                "cumulative_energy": cumulative_energy,
                "timestamp": int(time.time())
            }

            # --------------------------------------
            # Publish MQTT
            # --------------------------------------

            payload_json = json.dumps(payload, allow_nan=False)

            publish_and_wait(
                mqtt_client,
                MQTT_TOPIC,
                payload_json,
                qos=MQTT_QOS,
                retain=True,
            )
            logging.info(
                "MQTT published to %s: %s",
                MQTT_TOPIC,
                payload_json
            )

            consecutive_failures = 0

        except Exception as exc:

            consecutive_failures += 1

            logging.exception(
                "FusionSolar update failed "
                "(failure %d): %s",
                consecutive_failures,
                exc
            )

            fusion_client = None
            fusion_client_created_at = None

            plant_id = None

            if (
                consecutive_failures
                >= MAX_CONSECUTIVE_FAILURES
            ):

                logging.warning(
                    "FusionSolar failed %d times in a row. "
                    "Backing off for %d seconds.",
                    consecutive_failures,
                    FAILURE_BACKOFF
                )

                time.sleep(
                    FAILURE_BACKOFF
                )

                consecutive_failures = 0

                continue

        time.sleep(DAY_POLL_INTERVAL)


if __name__ == "__main__":
    main()
