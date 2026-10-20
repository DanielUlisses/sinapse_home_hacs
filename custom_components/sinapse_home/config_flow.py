"""Config flow: Sinapse Home account (same e-mail/password as the app)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SinapseApi, SinapseAuthError, SinapseError
from .const import DOMAIN, LOCAL_DEFAULT_PORT
from .coordinator import async_remember_local_address

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class SinapseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and re-authentication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SinapseOptionsFlow()

    async def _validate(self, username: str, password: str) -> str | None:
        api = SinapseApi(async_get_clientsession(self.hass), username, password)
        try:
            await api.login()
            await api.get_nodes()
        except SinapseAuthError:
            return "invalid_auth"
        except SinapseError:
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            error = await self._validate(username, user_input[CONF_PASSWORD])
            if error is None:
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """An ESP local control service on the LAN: remember where the node is.

        Any ESP RainMaker device announces this service type, so discovery never
        creates an entry; it only feeds the address of nodes the account knows.
        The account is the unique ID, so there is nothing to match on here.
        """
        node_id = discovery_info.properties.get("node_id")
        if not node_id:
            return self.async_abort(reason="not_rainmaker_node")
        async_remember_local_address(
            self.hass, node_id, str(discovery_info.ip_address), discovery_info.port
        )
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")
        return self.async_abort(reason="not_configured")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            error = await self._validate(username, user_input[CONF_PASSWORD])
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class SinapseOptionsFlow(OptionsFlow):
    """Manual LAN address, for networks where mDNS doesn't reach Home Assistant."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            options = {CONF_HOST: host, CONF_PORT: user_input[CONF_PORT]} if host else {}
            return self.async_create_entry(data=options)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HOST, description={"suggested_value": options.get(CONF_HOST)}
                ): str,
                vol.Optional(
                    CONF_PORT, default=options.get(CONF_PORT, LOCAL_DEFAULT_PORT)
                ): cv.port,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
