"""Plant API helpers."""

from __future__ import annotations

import time
from typing import Any

from fusionsolar_api.exceptions import FusionSolarException


def get_current_plant_data(client: Any, plant_id: str) -> dict:
    """Retrieve current plant KPI and energy flow data."""
    ts = round(time.time() * 1000)

    url = f"https://{client._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v1/overview/station-real-kpi"
    params = {
        "stationDn": plant_id,
        "clientTime": ts,
        "timeZone": 1,
        "_": ts,
    }
    r = client._session.get(url=url, params=params)
    r.raise_for_status()
    power_obj = r.json()

    if "data" not in power_obj:
        raise FusionSolarException("Failed to retrieve plant KPI data.")

    data = power_obj["data"]

    url = f"https://{client._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v3/overview/energy-flow"
    params = {"stationDn": plant_id, "featureId": "aifc", "_": ts}
    r = client._session.get(url=url, params=params)
    r.raise_for_status()
    flow_data = r.json()

    if "data" in flow_data and "flow" in flow_data["data"]:
        for node in flow_data["data"]["flow"].get("nodes", []):
            if node.get("name") == "neteco.pvms.devTypeLangKey.string":
                data["flow_solar_power"] = node.get("value")
                break

    return data


def get_station_list(client: Any) -> list:
    """Return every plant visible to the account, not only the first page."""
    plants = []
    page_size = 100

    for page in range(1, 101):
        r = client._session.post(
            url=f"https://{client._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v1/station/station-list",
            json={
                "curPage": page,
                "pageSize": page_size,
                "gridConnectedTime": "",
                "queryTime": get_day_start_sec(),
                "timeZone": 2,
                "sortId": "createTime",
                "sortDir": "DESC",
                "locale": "en_US",
            },
        )
        r.raise_for_status()
        obj_tree = r.json()

        if not obj_tree.get("success"):
            raise FusionSolarException("Failed to retrieve station list")

        page_plants = obj_tree.get("data", {}).get("list", [])
        if not isinstance(page_plants, list):
            raise FusionSolarException("FusionSolar returned an invalid station list")

        plants.extend(page_plants)

        if len(page_plants) < page_size:
            return plants

    raise FusionSolarException("FusionSolar station list exceeds 10,000 plants")


def get_day_start_sec() -> int:
    start_today = time.strftime("%Y-%m-%d 00:00:00", time.gmtime())
    struct_time = time.strptime(start_today, "%Y-%m-%d %H:%M:%S")
    return round(time.mktime(struct_time) * 1000)
