"""Config flow for iSolarCloud."""

import logging

from pysolarcloud import Server
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import selector
from homeassistant.util import Mapping

from .const import DOMAIN


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
        """Create an entry for the flow."""
        plant = data["token"]["result_data"]["auth_ps_list"][0]
        d = {
            **data,
            "plant": plant,
            "server": self.hass.data[self.DOMAIN]["server"].value,
            "client_id": self.flow_impl.client_id,
            "client_secret": self.flow_impl.client_secret,
        }
        await self.async_set_unique_id(str(plant))
        if self.source == config_entries.SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="plant_id_mismatch")
            self.logger.info(
                "async_oauth_create_entry callled with sourece=REAUTH and data=%s", d
            )
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=d)
        self.logger.info("async_oauth_create_entry callled with data=%s", d)
        return self.async_create_entry(title=self.flow_impl.name, data=d)

    async def async_step_reauth(
        self, entry_data: Mapping[str, vol.Any]
    ) -> config_entries.ConfigFlowResult:
        """Perform reauthentication upon an API authentication error."""
        self.hass.data.setdefault(self.DOMAIN, {})
        self.hass.data[self.DOMAIN]["server"] = Server(entry_data["server"])
        self.logger.warning("Re-authenticating with server=%s", entry_data["server"])
        return await self.async_step_pick_implementation(None)
