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

FUSIONSOLAR_SUBDOMAIN = os.getenv(
    "FUSIONSOLAR_SUBDOMAIN",
    "uni001eu5"
)

PLANT_ID = os.environ["FUSIONSOLAR_PLANT_ID"]


# --------------------------------------------------
# MQTT
# --------------------------------------------------

MQTT_HOST = os.getenv(
    "MQTT_HOST",
    "mqtt"
)

MQTT_PORT = int(
    os.getenv(
        "MQTT_PORT",
        "1883"
    )
)

MQTT_TOPIC = os.environ["MQTT_TOPIC"]

POLL_INTERVAL = int(
    os.getenv(
        "POLL_INTERVAL",
        "60"
    )
)


# --------------------------------------------------
# Self-repair
# --------------------------------------------------

MAX_CONSECUTIVE_FAILURES = 3
FAILURE_BACKOFF = 300


# --------------------------------------------------
# SenzoLink IDs from MQTT topic
#
# Expected:
# client/<client_id>/<gateway_id>/FusionSolar
# --------------------------------------------------

topic_parts = MQTT_TOPIC.strip("/").split("/")

if (
    len(topic_parts) < 4
    or topic_parts[0] != "client"
):
    raise RuntimeError(
        f"Unexpected MQTT_TOPIC format: {MQTT_TOPIC}"
    )

SENZOLINK_CLIENT_ID = topic_parts[1]
SENZOLINK_GATEWAY_ID = topic_parts[2]


DISCOVERY_BASE = (
    f"client/{SENZOLINK_CLIENT_ID}/"
    "ha/sensor/fusionsolar"
)

DEVICE_ID = (
    f"senzolink_{SENZOLINK_CLIENT_ID}_"
    f"{SENZOLINK_GATEWAY_ID}_fusionsolar"
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


# --------------------------------------------------
# Home Assistant MQTT Discovery
# --------------------------------------------------

def publish_discovery(client):

    device = {
        "identifiers": [DEVICE_ID],
        "name": "FusionSolar",
        "manufacturer": "Huawei",
        "model": "FusionSolar Plant"
    }

    sensors = {

        "power": {
            "name": "Trenutna snaga",
            "unique_id": f"{DEVICE_ID}_power",
            "device_class": "power",
            "state_class": "measurement",
            "unit_of_measurement": "kW",
            "value_template": "{{ value_json.power }}"
        },

        "daily_energy": {
            "name": "Današnja proizvodnja",
            "unique_id": f"{DEVICE_ID}_daily_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.daily_energy }}"
        },

        "month_energy": {
            "name": "Mjesečna proizvodnja",
            "unique_id": f"{DEVICE_ID}_month_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.month_energy }}"
        },

        "year_energy": {
            "name": "Godišnja proizvodnja",
            "unique_id": f"{DEVICE_ID}_year_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.year_energy }}"
        },

        "cumulative_energy": {
            "name": "Ukupna proizvodnja",
            "unique_id": f"{DEVICE_ID}_cumulative_energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value_json.cumulative_energy }}"
        }
    }

    for sensor_id, config in sensors.items():

        config["state_topic"] = MQTT_TOPIC
        config["device"] = device

        discovery_topic = (
            f"{DISCOVERY_BASE}/{sensor_id}/config"
        )

        result = client.publish(
            discovery_topic,
            json.dumps(config),
            qos=1,
            retain=True
        )

        result.wait_for_publish()

    logging.info(
        "FusionSolar Home Assistant discovery published"
    )


# --------------------------------------------------
# MQTT client
# --------------------------------------------------

def create_mqtt_client():

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        60
    )

    client.loop_start()

    publish_discovery(client)

    return client


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():

    mqtt_client = create_mqtt_client()

    fusion_client = None

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

            # --------------------------------------
            # Get plant data
            # --------------------------------------

            data = (
                fusion_client
                .get_current_plant_data(
                    PLANT_ID
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
                qos=1,
                retain=True
            )

            result.wait_for_publish()

            logging.info(
                "Solar: %.3f kW | "
                "Today: %s kWh | "
                "Month: %s kWh",
                payload["power"],
                payload["daily_energy"],
                payload["month_energy"]
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
