"""Application credentials platform for the iSolarCloud integration."""

import logging

from pysolarcloud import AbstractAuth, Server

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.config_entry_oauth2_flow import (
    URL,
    AbstractOAuth2Implementation,
    _encode_jwt,
    async_get_redirect_uri,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class OAuth2Impl(AbstractAuth, AbstractOAuth2Implementation):
    """Implementation of the OAuth2 flow for iSolarCloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        client_id: str,
        client_secret: str,
        server: str,
    ) -> None:
        """Initialize local auth implementation."""
        self.hass = hass
        self._domain = domain
        self.server = server
        self.client_id = client_id
        self.client_secret = client_secret
        websession = aiohttp_client.async_get_clientsession(hass)
        self.app_id, appkey = client_id.split("@")
        super().__init__(websession, server, appkey, client_secret, self.app_id)

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return "iSolarCloud"

    @property
    def domain(self) -> str:
        """Domain of the implementation."""
        return DOMAIN

    @property
    def redirect_uri(self) -> str:
        """Return the redirect uri."""
        return async_get_redirect_uri(self.hass)

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate a url for the user to authorize."""
        # Not sure what url to use for China and International servers
        # Please comment here if you know the correct url
        # https://github.com/bugjam/hass-isolarcloud/issues/4
        match self.server:
            case Server.China.value:
                cloud_id = 1
            case Server.International.value:
                cloud_id = 2
            case Server.Europe.value:
                cloud_id = 3
                cloud_url = "https://web3.isolarcloud.eu/#/authorized-app"
            case Server.Australia.value:
                cloud_id = 7
                cloud_url = "https://auweb3.isolarcloud.com/#/authorized-app"
            case _:
                _LOGGER.error("Unknown server: %s", self.server)
                raise ConfigEntryAuthFailed(f"Unknown server: {self.server}")
        state = _encode_jwt(
            self.hass,
            {"flow_id": flow_id, "redirect_uri": self.redirect_uri},
        )
        query = {
            "applicationId": self.app_id,
            "cloudId": cloud_id,
            "redirectUrl": self.redirect_uri,
        }
        url = URL(cloud_url).with_query(query)
        # Auth endpoint expects state first and other params after fragment
        port = f":{url.port}" if url.explicit_port is not None else ""
        url = f"{url.scheme}://{url.host}{port}?state={state}#{url.fragment}?{url.raw_query_string}"
        _LOGGER.debug("Generated authorize url: %s", url)
        return url

    async def async_resolve_external_data(self, external_data) -> dict:
        """Resolve external data to tokens."""
        result = await self.async_fetch_tokens(external_data["code"], self.redirect_uri)
        if "error" in result:
            _LOGGER.error("Error fetching tokens: %s", result)
            raise ConfigEntryAuthFailed("Failed to fetch tokens")
        return result

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token."""
        _LOGGER.debug("Refreshing token")
        result = await self.async_refresh_tokens(token["refresh_token"])
        if "error" in result:
            _LOGGER.error("Error refreshing token: %s", result)
            raise ConfigEntryAuthFailed("Failed to refresh token")
        return result

    async def async_get_access_token(self) -> str:
        """Return a valid access token.

        N/A in this class as it is only used for setting up the OAuth2 flow.
        """
        return None


async def async_get_auth_implementation(hass: HomeAssistant, auth_domain, credential):
    """Get the iSolarCloud OAuth2 implementation."""
    config_entry = hass.config_entries.async_get_entry(auth_domain)
    if config_entry:
        server = config_entry.data["server"]
    elif DOMAIN in hass.data:
        server = hass.data[DOMAIN]["server"]
    else:
        server = None
    _LOGGER.debug(
        "Creating OAuth2 implementation for %s, server=%s", auth_domain, server
    )
    return OAuth2Impl(
        hass,
        auth_domain,
        credential.client_id,
        credential.client_secret,
        server,
    )
