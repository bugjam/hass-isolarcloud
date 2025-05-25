"""Sensor platform for iSolarCloud."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import lru_cache
import logging

from pysolarcloud import PySolarCloudException
from pysolarcloud.plants import Plants

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN
from .services import async_register_services

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

    # Register services from services.py
    await async_register_services(hass, coordinator, import_sensors=ENERGY_SENSORS)

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

    @lru_cache
    def get_entity_id(self, unique_id):
        """Get the entity id of a sensor."""
        entity_registry = er.async_get(self.hass)
        return entity_registry.async_get_entity_id(
            domain="sensor", platform=DOMAIN, unique_id=unique_id
        )
