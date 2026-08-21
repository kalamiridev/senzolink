import os
import json
import time
import logging

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from fusionsolar_api.client import FusionSolarClient


# --------------------------------------------------
# Environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# FusionSolar
# --------------------------------------------------

FUSIONSOLAR_USERNAME = os.environ["FUSIONSOLAR_USERNAME"]
FUSIONSOLAR_PASSWORD = os.environ["FUSIONSOLAR_PASSWORD"]

FUSIONSOLAR_SUBDOMAIN = os.environ["FUSIONSOLAR_SUBDOMAIN"].strip()

FUSIONSOLAR_PLANT_NAME = os.getenv(
    "FUSIONSOLAR_PLANT_NAME",
    ""
).strip()


# --------------------------------------------------
# MQTT
# --------------------------------------------------

MQTT_HOST = os.environ["MQTT_HOST"].strip()

MQTT_PORT = int(
    os.getenv(
        "MQTT_PORT",
        "1883"
    )
)

MQTT_TOPIC = os.environ["MQTT_TOPIC"]

MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

MQTT_TLS_ENABLED = os.getenv(
    "MQTT_TLS_ENABLED",
    "false"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off"
}

try:
    MQTT_QOS = int(os.getenv("MQTT_QOS", "0").strip())
except ValueError as exc:
    raise RuntimeError("MQTT_QOS must be 0, 1, or 2") from exc

if MQTT_QOS not in {0, 1, 2}:
    raise RuntimeError("MQTT_QOS must be 0, 1, or 2")

POLL_INTERVAL = int(
    os.getenv(
        "POLL_INTERVAL",
        "180"
    )
)


# --------------------------------------------------
# Home Assistant MQTT Discovery
# --------------------------------------------------

HA_DISCOVERY_ENABLED = os.getenv(
    "HA_DISCOVERY_ENABLED",
    "true"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off"
}

HA_DEVICE_ID = "fusionsolar"

DISCOVERY_BASE = "homeassistant/sensor/fusionsolar"


# --------------------------------------------------
# Self-repair
# --------------------------------------------------

MAX_CONSECUTIVE_FAILURES = 3
FAILURE_BACKOFF = 300


# --------------------------------------------------
# MQTT topic validation
# --------------------------------------------------

for name, value in {
    "FUSIONSOLAR_SUBDOMAIN": FUSIONSOLAR_SUBDOMAIN,
    "MQTT_HOST": MQTT_HOST,
    "MQTT_TOPIC": MQTT_TOPIC,
}.items():
    if not value.strip():
        raise RuntimeError(f"{name} must not be empty")

if MQTT_PASSWORD and not MQTT_USERNAME:
    raise RuntimeError(
        "MQTT_USERNAME is required when MQTT_PASSWORD is set"
    )

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

        result = client.publish(
            discovery_topic,
            json.dumps(config),
            qos=MQTT_QOS,
            retain=True
        )

        result.wait_for_publish()

    logging.info(
        "Home Assistant discovery published under %s",
        DISCOVERY_BASE
    )


# --------------------------------------------------
# MQTT client
# --------------------------------------------------

def create_mqtt_client():

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    if MQTT_USERNAME:
        client.username_pw_set(
            MQTT_USERNAME,
            MQTT_PASSWORD or None
        )

    if MQTT_TLS_ENABLED:
        client.tls_set()

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        60
    )

    client.loop_start()

    if HA_DISCOVERY_ENABLED:
        publish_discovery(client)

    return client


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():

    mqtt_client = create_mqtt_client()

    fusion_client = None

    plant_id = None

    consecutive_failures = 0

    while True:

        try:

            # --------------------------------------
            # Create/recreate FusionSolar session
            # --------------------------------------

            if fusion_client is None:

                fusion_client = (
                    create_fusionsolar_client()
                )

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

            flow_power = data.get(
                "flow_solar_power"
            )

            current_power = data.get(
                "currentPower"
            )

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

                logging.error(
                    "FusionSolar returned no usable power value"
                )

                logging.error(
                    "FusionSolar response: %s",
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str
                    )
                )

                raise RuntimeError(
                    "FusionSolar did not return solar power"
                )

            # --------------------------------------
            # Energy values
            # --------------------------------------

            daily_energy = data.get(
                "dailyEnergy"
            )

            month_energy = data.get(
                "monthEnergy"
            )

            year_energy = data.get(
                "yearEnergy"
            )

            cumulative_energy = data.get(
                "cumulativeEnergy"
            )

            # --------------------------------------
            # Build MQTT payload
            # --------------------------------------

            payload = {

                "power": float(
                    power
                ),

                "daily_energy": (
                    float(daily_energy)
                    if daily_energy is not None
                    else None
                ),

                "month_energy": (
                    float(month_energy)
                    if month_energy is not None
                    else None
                ),

                "year_energy": (
                    float(year_energy)
                    if year_energy is not None
                    else None
                ),

                "cumulative_energy": (
                    float(cumulative_energy)
                    if cumulative_energy is not None
                    else None
                ),

                "timestamp": int(
                    time.time()
                )
            }

            # --------------------------------------
            # Publish MQTT
            # --------------------------------------

            payload_json = json.dumps(
                payload
            )

            result = mqtt_client.publish(
                MQTT_TOPIC,
                payload_json,
                qos=MQTT_QOS,
                retain=True
            )

            result.wait_for_publish()

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

        time.sleep(
            POLL_INTERVAL
        )


if __name__ == "__main__":
    main()
