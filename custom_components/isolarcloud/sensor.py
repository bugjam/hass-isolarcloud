"""Sensor platform for iSolarCloud."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from functools import lru_cache, partial
from itertools import groupby
import logging
from sqlite3 import DatabaseError

from pysolarcloud import PySolarCloudException
from pysolarcloud.plants import Plants
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    SQLAlchemyError,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.sensor import (
    HomeAssistantError,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util.dt import as_local, as_utc, now, start_of_local_day

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ENERGY_SENSORS = [
    "feed_in_energy_total",
    "cumulative_discharge",
    "energy_storage_cumulative_charge",
    "total_purchased_energy",
    "total_load_consumption",
    "total_yield",
    "total_direct_energy_consumption",
]
POWER_SENSORS = ["power", "load_power"]
BATTERY_SENSORS = ["battery_level_soc"]
ALL_SENSORS = ENERGY_SENSORS + POWER_SENSORS + BATTERY_SENSORS


def unit_of(sensor: str):
    """Return the unit of measurement for a sensor."""
    if sensor in ENERGY_SENSORS:
        return UnitOfEnergy.WATT_HOUR
    if sensor in POWER_SENSORS:
        return UnitOfPower.WATT
    if sensor in BATTERY_SENSORS:
        return PERCENTAGE
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the sensor platform."""
    coordinator = Coordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()
    device = DeviceInfo(
        identifiers={(DOMAIN, config.data["plant"])},
        name=coordinator.plant_name,
    )

    async def import_historical_data(call: ServiceCall):
        """Import historical data."""
        start = as_local(call.data["start"])
        end = as_local(call.data["end"])
        adjusted = False
        midnight_today = as_local(start_of_local_day())
        if end > midnight_today:
            end = midnight_today
            adjusted = True

        imported_count = await coordinator.async_import_historical_data(start, end)
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
    async_add_entities(
        [
            ISolarCloudSensor(coordinator, device, s, SensorDeviceClass.ENERGY)
            for s in ENERGY_SENSORS
        ]
        + [
            ISolarCloudSensor(coordinator, device, s, SensorDeviceClass.POWER)
            for s in POWER_SENSORS
        ]
        + [
            ISolarCloudSensor(coordinator, device, s, SensorDeviceClass.BATTERY)
            for s in BATTERY_SENSORS
        ],
    )
    return True


class ISolarCloudSensor(CoordinatorEntity, SensorEntity):
    """Generic Sensor for iSolarCloud."""

    def __init__(
        self, coordinator: Coordinator, device: DeviceInfo, id: str, sensor_type: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.id = id
        self._attr_device_info = device
        self._attr_unique_id = f"{coordinator.plant_id}_{id}"
        self._attr_translation_key = id
        self._attr_has_entity_name = True
        self._attr_device_class = sensor_type
        self._attr_native_unit_of_measurement = unit_of(id)

        # Set attributes based on sensor type
        if sensor_type == SensorDeviceClass.ENERGY:
            self._attr_state_class = SensorStateClass.TOTAL
            self._value_transform = lambda v: v
        elif sensor_type == SensorDeviceClass.POWER:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._value_transform = lambda v: v
        elif sensor_type == SensorDeviceClass.BATTERY:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._value_transform = lambda v: v * 100.0
        else:
            self._value_transform = lambda v: v

        # Get initial sensor value from coordinator
        if (
            self.coordinator.data
            and self.id in self.coordinator.data
            and self.coordinator.data[self.id].get("value") is not None
        ):
            self._attr_native_value = self._value_transform(
                self.coordinator.data[self.id]["value"]
            )
            self._attr_available = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self.coordinator.data
            and self.id in self.coordinator.data
            and self.coordinator.data[self.id].get("value") is not None
        ):
            self._attr_native_value = self._value_transform(
                self.coordinator.data[self.id]["value"]
            )
            self._attr_available = True
        else:
            self._attr_native_value = None
            self._attr_available = False
        self.async_write_ha_state()


class Coordinator(DataUpdateCoordinator):
    """Update Coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigType) -> None:
        """Initialize my coordinator."""
        if config_entry.options and "update_interval" in config_entry.options:
            update_interval = timedelta(
                seconds=float(config_entry.options["update_interval"])
            )
            _LOGGER.info(
                "Update interval configured to %s seconds", update_interval.seconds
            )
        else:
            update_interval = timedelta(minutes=5)
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="isolarcloud",
            config_entry=config_entry,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=update_interval,
            always_update=False,
        )
        self.plant_id = config_entry.data["plant"]
        self.plants_api: Plants = config_entry.runtime_data.api
        self.plant_name = None

    async def _async_setup(self):
        """Set up the coordinator.

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """
        data = await self.plants_api.async_get_plant_details(self.plant_id)
        pdata = data[0]
        self.plant_name = pdata["ps_name"]

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with asyncio.timeout(10):
                data = await self.plants_api.async_get_realtime_data(
                    self.plant_id, measure_points=ALL_SENSORS
                )
                _LOGGER.debug("Data: %s", data)
                p = self.plant_id
                if p in data:
                    return data[p]
                raise UpdateFailed(f"Plant not found in data: {data}")
        #        except ApiAuthError as err:
        # Raising ConfigEntryAuthFailed will cancel future updates
        # and start a config flow with SOURCE_REAUTH (async_step_reauth)
        #            raise ConfigEntryAuthFailed from err
        except PySolarCloudException as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def async_import_historical_data(
        self, start: datetime, end: datetime, delete: bool = False
    ) -> int:
        """Import historical data."""
        interval_length = int(timedelta(hours=3).total_seconds())
        period = end - start
        intervals = range(int(period.total_seconds()) // interval_length)
        if delete:
            for sensor in ENERGY_SENSORS:
                await self.async_delete_statistics_range(
                    self.get_entity_id(f"{self.plant_id}_{sensor}"), start, end
                )
        baselines = await self.get_baselines(ENERGY_SENSORS)
        if None in baselines.values():
            _LOGGER.error("Could not get baseline values for all sensors")
            return 0
        n = 0
        for i in intervals:
            start_time = start + timedelta(seconds=i * interval_length)
            end_time = min(start_time + timedelta(hours=3), end)
            try:
                async with asyncio.timeout(10):
                    data = await self.plants_api.async_get_historical_data(
                        self.plant_id,
                        start_time,
                        end_time,
                        measure_points=ENERGY_SENSORS,
                        interval=timedelta(minutes=60),
                    )
                    _LOGGER.debug("Retrieved historical data: %s", data)
            except PySolarCloudException as err:
                _LOGGER.error("Error retrieving historical data: %s", err)
                raise HomeAssistantError from err
            data = data[self.plant_id]
            data.sort(key=lambda x: x["code"])
            hist = groupby(data, key=lambda x: x["code"])
            for sensor, frames in hist:
                baseline = baselines[sensor]
                _LOGGER.debug(
                    "Adjusting value for %s with baseline %f", sensor, baseline
                )
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
                    statistic_id=self.get_entity_id(f"{self.plant_id}_{sensor}"),
                    name=sensor,
                    has_sum=True,
                    has_mean=False,
                )
                _LOGGER.debug(
                    "Adding %d statistics for %s", len(stats), metadata["statistic_id"]
                )
                async_import_statistics(self.hass, metadata, stats)
                n += len(stats)
        return n

    async def get_baselines(self, sensors) -> dict[str, float | None]:
        """Get baseline values for all energy sensors."""
        # Get all entity IDs first
        entity_ids = []
        sensor_map = {}  # Map entity_ids back to sensor names
        for sensor in sensors:
            entity_id = self.get_entity_id(f"{self.plant_id}_{sensor}")
            if entity_id:
                entity_ids.append(entity_id)
                sensor_map[entity_id] = sensor

        if not entity_ids:
            return {}

        # Get statistics for all sensors
        start_time = now() - timedelta(days=1)
        stats = await self.hass.async_add_executor_job(
            partial(
                statistics_during_period,
                self.hass,
                start_time=start_time,
                end_time=None,
                statistic_ids=entity_ids,
                period="hour",
                units=None,
                types=["sum", "state"],
            )
        )

        # Process results for each sensor
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

    async def async_delete_statistics_range(
        self, statistic_id: str, start_time: datetime, end_time: datetime
    ):
        """Delete existing statistics rows for a statistic_id in a given time range."""
        await get_instance(self.hass).async_add_executor_job(
            _delete_statistics_range_blocking,
            self.hass,
            statistic_id,
            start_time,
            end_time,
        )

    @lru_cache
    def get_entity_id(self, unique_id):
        """Get the entity id of a sensor."""
        entity_registry = er.async_get(self.hass)
        return entity_registry.async_get_entity_id(
            domain="sensor", platform=DOMAIN, unique_id=unique_id
        )


async def _delete_statistics_range_blocking(
    hass: HomeAssistant, statistic_id: str, start_time: datetime, end_time: datetime
):
    """Delete existing statistics rows for a statistic_id in a given time range.

    This method is blocking and should be run in a separate thread with async_add_executor_job.
    """
    # NOTE: This function runs inside executor thread, but HA may still warn about
    #       "Detected code that accesses the database without the database executor"
    #       due to SQLAlchemy internals. This is a known issue and can be ignored.
    db = get_instance(hass).async_get_database_engine()
    connection = db.raw_connection()

    try:
        cursor = connection.cursor()
        start_ts = start_time.timestamp()
        end_ts = end_time.timestamp()

        # Get metadata_id from statistics_meta
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

        # Delete rows in statistics table for that metadata_id and time range
        cursor.execute(
            "DELETE FROM statistics WHERE metadata_id = ? AND start_ts >= ? AND start_ts < ?",
            (metadata_id, start_ts, end_ts),
        )
        connection.commit()
        _LOGGER.debug("Deletion completed")

    except (SQLAlchemyError, DatabaseError) as e:
        _LOGGER.error("Error while deleting statistics", exc_info=e)
        connection.rollback()
    finally:
        cursor.close()
        connection.close()
