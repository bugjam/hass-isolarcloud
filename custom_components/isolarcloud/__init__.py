"""The iSolarCloud integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow

from . import api
from .const import DOMAIN

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

type ISolarCloudConfigEntry = ConfigEntry[api.AsyncConfigEntryAuth]


async def async_setup_entry(hass: HomeAssistant, entry: ISolarCloudConfigEntry) -> bool:
    """Set up iSolarCloud from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    entry.runtime_data = api.AsyncConfigEntryAuth(
        aiohttp_client.async_get_clientsession(hass),
        session,
        entry.data["server"],
        entry.data["client_id"],
        entry.data["client_secret"],
        entry.data["plant"],
    )
    # Fetch access token - this triggers token refresh or re-authentcation if needed
    await entry.runtime_data.async_get_access_token()

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
