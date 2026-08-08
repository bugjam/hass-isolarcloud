"""The iSolarCloud integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow

from . import api
from .const import DOMAIN
from .sensor import Coordinator

_LOGGER = logging.getLogger(__name__)
_PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("server"): str,
                vol.Required("client_id"): str,
                vol.Required("client_secret"): str,
                vol.Required("plant"): str,
                vol.Optional("plants"): vol.All(vol.Coerce(list), vol.Length(min=1)),
                vol.Optional("token"): dict,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

@dataclass
class RuntimeData:
    """Runtime data stored on the config entry."""

    auth: api.AsyncConfigEntryAuth
    coordinator: Coordinator


type ISolarCloudConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ISolarCloudConfigEntry) -> bool:
    """Set up iSolarCloud from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    auth = api.AsyncConfigEntryAuth(
        aiohttp_client.async_get_clientsession(hass),
        session,
        entry.data["server"],
        entry.data["client_id"],
        entry.data["client_secret"],
        entry.data["plant"],
    )
    # Fetch access token - this triggers token refresh or re-authentcation if needed
    await auth.async_get_access_token()

    plants = entry.data.get("plants", [entry.data["plant"]])
    coordinator = Coordinator(hass, entry, plants, auth.api)
    # Do the first refresh here, in the top-level entry setup, *before*
    # forwarding to the sensor platform. If this raises ConfigEntryNotReady,
    # it needs to propagate from here so Home Assistant's automatic
    # retry-with-backoff applies. Previously this call lived inside
    # sensor.async_setup_entry (the forwarded platform setup), where
    # raising ConfigEntryNotReady doesn't trigger that retry mechanism —
    # HA just logs an error and leaves the entry "loaded" with permanently
    # unavailable entities after any transient failure (e.g. a DNS blip),
    # requiring a manual reload or restart to recover.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = RuntimeData(auth=auth, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ISolarCloudConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > 1:
        return False

    if config_entry.minor_version < 2:
        new_data = {**config_entry.data}
        new_data["plants"] = [config_entry.data["plant"]]

        hass.config_entries.async_update_entry(
            config_entry, data=new_data, minor_version=2, version=1
        )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options are updated (e.g. changed update_interval)."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
