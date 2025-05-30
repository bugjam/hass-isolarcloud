"""Config flow for iSolarCloud."""

import logging

from pysolarcloud import Server
from pysolarcloud.plants import Plants
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import selector
from homeassistant.util import Mapping

from .const import DOMAIN


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for setting update interval."""

    OPTIONS_SCHEMA = vol.Schema(
        {
            vol.Required("update_interval", default=300): selector(
                {
                    "number": {
                        "min": 1,
                        "max": 3600,
                        "mode": "box",
                        "unit_of_measurement": "seconds",
                    }
                }
            )
        }
    )

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._update_interval = 300  # Default update interval in seconds

    async def async_step_init(self, user_input=None) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                self.OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle iSolarCloud OAuth2 authentication."""

    DOMAIN = DOMAIN

    server_schema = {
        vol.Required("server"): selector(
            {"select": {"options": [name for name, _ in Server.__members__.items()]}}
        )
    }

    def _plant_schema(self, plants: list[dict]) -> vol.Schema:
        """Return a schema for selecting multiple plants."""
        return vol.Schema(
            {
                vol.Required(
                    "plants", default=[str(plant["ps_id"]) for plant in plants]
                ): selector(
                    {
                        "select": {
                            "options": [
                                {
                                    "label": plant["ps_name"],
                                    "value": str(plant["ps_id"]),
                                }
                                for plant in plants
                            ],
                            "multiple": True,
                        }
                    }
                )
            }
        )

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    async def async_step_user(self, user_input=None):
        """Handle a flow start."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema(self.server_schema)
            )
        self.hass.data.setdefault(self.DOMAIN, {})
        self.hass.data[self.DOMAIN]["server"] = Server[user_input["server"]]
        try:
            return await self.async_step_pick_implementation(None)
        except ConfigEntryAuthFailed as ex:
            return self.async_abort(reason=ex.translation_key)

    async def async_oauth_create_entry(
        self, data: dict
    ) -> config_entries.ConfigFlowResult:
        """Handle OAuth completion."""
        plants = data["token"]["result_data"]["auth_ps_list"]
        if len(plants) > 1 and self.source != config_entries.SOURCE_REAUTH:
            self._oauth_data = data
            return await self.async_step_select_plants()
        return await self._async_create_entry(data, plants)

    async def async_step_select_plants(self, user_input=None):
        """Step to select plants."""
        if user_input is not None:
            return await self._async_create_entry(
                self._oauth_data, user_input["plants"]
            )
        api = Plants(self.flow_impl)
        plants = await api.async_get_plants()
        self.logger.debug("Plants: %s", plants)
        return self.async_show_form(
            step_id="select_plants",
            data_schema=self._plant_schema(plants),
        )

    async def _async_create_entry(self, data, plants):
        d = {
            **data,
            "plant": plants[0],
            "plants": plants,
            "server": self.hass.data[self.DOMAIN]["server"].value,
            "client_id": self.flow_impl.client_id,
            "client_secret": self.flow_impl.client_secret,
        }
        await self.async_set_unique_id(plants[0])
        if self.source == config_entries.SOURCE_REAUTH:
            self.logger.info(
                "async_oauth_create_entry called with source=REAUTH and data=%s", d
            )
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=d)
        self.logger.info("async_oauth_create_entry called with data=%s", d)
        return self.async_create_entry(title=self.flow_impl.name, data=d)

    async def async_step_reauth(
        self, entry_data: Mapping[str, vol.Any]
    ) -> config_entries.ConfigFlowResult:
        """Perform reauthentication upon an API authentication error."""
        self.hass.data.setdefault(self.DOMAIN, {})
        self.hass.data[self.DOMAIN]["server"] = Server(entry_data["server"])
        self.logger.warning("Re-authenticating with server=%s", entry_data["server"])
        return await self.async_step_pick_implementation(None)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler()
