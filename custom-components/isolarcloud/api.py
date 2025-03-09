"""API for iSolarCloud bound to Home Assistant OAuth."""

from aiohttp import ClientSession
import pysolarcloud
from pysolarcloud.plants import Plants

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow


class AsyncConfigEntryAuth(pysolarcloud.AbstractAuth):
    """Provide iSolarCloud authentication tied to an OAuth2 based config entry."""

    def __init__(
        self,
        websession: ClientSession,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
        server: str,
        client_id: str,
        client_secret: str,
        plant: str,
    ) -> None:
        """Initialize iSolarCloud auth."""
        app_id, appkey = client_id.split("@")
        super().__init__(
            websession,
            server,
            appkey,
            client_secret,
            app_id,
        )
        self._oauth_session = oauth_session
        oauth_session.implementation.server = server
        self.plant = plant
        self.api = Plants(self)

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        if (
            not self._oauth_session.token
            or "access_token" not in self._oauth_session.token
        ):
            raise ConfigEntryAuthFailed("No OAuth2 token available")
        await self._oauth_session.async_ensure_token_valid()

        if "access_token" not in self._oauth_session.token:
            raise ConfigEntryAuthFailed("No access token in OAuth2 session")

        return self._oauth_session.token["access_token"]
