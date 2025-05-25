"""Service registrations for iSolarCloud integration."""

import asyncio
from datetime import datetime, timedelta
from functools import partial
from itertools import groupby
import logging

from pysolarcloud.plants import Plants
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.sensor import HomeAssistantError
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util.dt import as_local, as_utc, now, start_of_local_day

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_import_historical_data(
    hass: HomeAssistant,
    plants_api: Plants,
    plant_id: str,
    get_entity_id_fn,
    import_sensors: list[str],
    start: datetime,
    end: datetime,
    delete: bool = False,
):
    """Import historical data."""
    interval_length = int(timedelta(hours=3).total_seconds())
    period = end - start
    intervals = range(int(period.total_seconds()) // interval_length)
    if delete:
        for sensor in import_sensors:
            await async_delete_statistics_range(
                hass,
                get_entity_id_fn(f"{plant_id}_{sensor}"),
                start,
                end,
            )
    baselines = await get_baselines(hass, get_entity_id_fn, plant_id, import_sensors)
    if None in baselines.values():
        _LOGGER.error("Could not get baseline values for all sensors")
        return 0
    n = 0
    for i in intervals:
        start_time = start + timedelta(seconds=i * interval_length)
        end_time = min(start_time + timedelta(hours=3), end)
        try:
            async with asyncio.timeout(10):
                data = await plants_api.async_get_historical_data(
                    plant_id,
                    start_time,
                    end_time,
                    measure_points=import_sensors,
                    interval=timedelta(minutes=60),
                )
                _LOGGER.debug("Retrieved historical data: %s", data)
        except Exception as err:
            _LOGGER.error("Error retrieving historical data: %s", err)
            raise HomeAssistantError from err
        data = data[plant_id]
        data.sort(key=lambda x: x["code"])
        hist = groupby(data, key=lambda x: x["code"])
        for sensor, frames in hist:
            baseline = baselines[sensor]
            _LOGGER.debug("Adjusting value for %s with baseline %f", sensor, baseline)
            fs = list(frames)
            stats = [
                StatisticData(
                    start=as_utc(as_local(f["timestamp"])),
                    sum=f["value"] - baseline,
                    state=f["value"],
                )
                for f in fs
            ]
            metadata = StatisticMetaData(
                unit_of_measurement=fs[0]["unit"],
                source="recorder",
                statistic_id=get_entity_id_fn(f"{plant_id}_{sensor}"),
                name=sensor,
                has_sum=True,
                has_mean=False,
            )
            _LOGGER.debug(
                "Adding %d statistics for %s", len(stats), metadata["statistic_id"]
            )
            async_import_statistics(hass, metadata, stats)
            n += len(stats)
    return n


async def get_baselines(
    hass: HomeAssistant, get_entity_id_fn, plant_id: str, sensors: list[str]
) -> dict[str, float | None]:
    """Get baseline values for all energy sensors.

    Baseline is calculated as the difference between the latest state and sum.
    State stores the actual sensor value
    Sum represents the delta since the sensor was added to Home Assistant.
    """
    entity_ids = []
    sensor_map = {}
    for sensor in sensors:
        entity_id = get_entity_id_fn(f"{plant_id}_{sensor}")
        if entity_id:
            entity_ids.append(entity_id)
            sensor_map[entity_id] = sensor
    if not entity_ids:
        return {}
    start_time = now() - timedelta(days=1)
    stats = await hass.async_add_executor_job(
        partial(
            statistics_during_period,
            hass,
            start_time=start_time,
            end_time=None,
            statistic_ids=entity_ids,
            period="hour",
            units=None,
            types=["sum", "state"],
        )
    )
    baselines = {}
    for entity_id in entity_ids:
        sensor_stats = stats.get(entity_id)
        if not sensor_stats:
            baselines[sensor_map[entity_id]] = None
            continue
        latest_entry = sensor_stats[-1]
        latest_sum = latest_entry.get("sum")
        latest_state = latest_entry.get("state")
        if latest_sum is None or latest_state is None:
            baselines[sensor_map[entity_id]] = None
            continue
        try:
            baseline = float(latest_state) - float(latest_sum)
            _LOGGER.debug(
                "Calculated baseline for %s: state=%.2f, sum=%.2f → baseline=%.2f",
                entity_id,
                latest_state,
                latest_sum,
                baseline,
            )
            baselines[sensor_map[entity_id]] = baseline
        except (TypeError, ValueError):
            _LOGGER.error(
                "Could not calculate baseline for %s: state=%s, sum=%s",
                entity_id,
                latest_state,
                latest_sum,
            )
            baselines[sensor_map[entity_id]] = None
    return baselines


async def async_delete_statistics_range(hass, statistic_id, start_time, end_time):
    """Delete existing statistics rows for a statistic_id in a given time range."""
    # Add job to the executor to avoid blocking the event loop
    await get_instance(hass).async_add_executor_job(
        _delete_statistics_range_blocking,
        hass,
        statistic_id,
        start_time,
        end_time,
    )


def _delete_statistics_range_blocking(
    hass: HomeAssistant, statistic_id: str, start_time: datetime, end_time: datetime
):
    """Delete existing statistics rows for a statistic_id in a given time range."""
    db = get_instance(hass).async_get_database_engine()
    connection = db.raw_connection()
    try:
        cursor = connection.cursor()
        start_ts = start_time.timestamp()
        end_ts = end_time.timestamp()
        cursor.execute(
            "SELECT id FROM statistics_meta WHERE statistic_id = ?", (statistic_id,)
        )
        result = cursor.fetchone()
        if not result:
            _LOGGER.warning("No metadata_id found for statistic_id %s", statistic_id)
            return
        metadata_id = result[0]
        _LOGGER.warning(
            "Deleting statistics for metadata_id %s between %s and %s",
            metadata_id,
            start_time,
            end_time,
        )
        cursor.execute(
            "DELETE FROM statistics WHERE metadata_id = ? AND start_ts >= ? AND start_ts < ?",
            (metadata_id, start_ts, end_ts),
        )
        connection.commit()
        _LOGGER.debug("Deletion completed")
    except Exception as e:
        _LOGGER.error("Error while deleting statistics", exc_info=e)
        connection.rollback()
    finally:
        cursor.close()
        connection.close()


async def async_register_services(
    hass: HomeAssistant, coordinator, import_sensors: list[str]
):
    """Register Home Assistant services for the iSolarCloud integration."""

    async def import_historical_data(call: ServiceCall):
        """Import historical data."""
        start = as_local(call.data["start"])
        end = as_local(call.data["end"])
        adjusted = False
        midnight_today = as_local(start_of_local_day())
        if end > midnight_today:
            end = midnight_today
            adjusted = True
        imported_count = await async_import_historical_data(
            hass,
            coordinator.plants_api,
            coordinator.plant_id,
            coordinator.get_entity_id,
            import_sensors,
            start,
            end,
            call.data.get("delete", False),
        )
        result_msg = f"Imported {imported_count} statistics from {start} to {end}."
        if adjusted:
            result_msg += " Note: End time was adjusted as the API supports only data before the current day."
        _LOGGER.info(result_msg)

    hass.services.async_register(
        DOMAIN,
        "import_historical_data",
        import_historical_data,
        schema=vol.Schema(
            {
                vol.Required("start"): cv.datetime,
                vol.Required("end"): cv.datetime,
                vol.Optional("delete", default=False): cv.boolean,
            }
        ),
    )

    async def list_data_points(call: ServiceCall):
        """List available data points for the plant."""
        if call.data.get("measure_points"):
            mp = call.data["measure_points"]
        else:
            mp = None
        try:
            data_points = await coordinator.plants_api.async_get_realtime_data(
                coordinator.plant_id, measure_points=mp
            )
            if coordinator.plant_id in data_points:
                _LOGGER.debug(
                    "Data points for plant %s: %s", coordinator.plant_id, data_points
                )
                return {
                    "data_points": [
                        {
                            "code": dp["code"],
                            "value": dp["value"],
                            "unit": dp["unit"],
                        }
                        for dp in data_points[coordinator.plant_id].values()
                        if dp["value"] is not None
                    ],
                }
            else:
                _LOGGER.error(
                    "Plant ID %s not found in data points: %s",
                    coordinator.plant_id,
                    data_points,
                )
                return {
                    "data_points": [],
                }
        except Exception as err:
            _LOGGER.error("Error retrieving data points: %s", err)
            raise HomeAssistantError from err

    hass.services.async_register(
        DOMAIN,
        "list_data_points",
        list_data_points,
        schema=vol.Schema(
            {
                vol.Optional("measure_points", default=[]): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
