"""Config flow for Arylic - LP Series."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import voluptuous as vol

from .client import LPClient, LPError, device_id, discover
from .const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

POWER_DOMAINS = ["switch", "light", "input_boolean"]


def _power_schema(defaults: dict[str, Any]) -> vol.Schema:
    power = defaults.get(CONF_POWER_SWITCH)
    return vol.Schema(
        {
            vol.Optional(
                CONF_POWER_SWITCH,
                description={"suggested_value": power} if power else None,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=POWER_DOMAINS)),
            vol.Required(
                CONF_AIRPLAY_FALLBACK,
                default=defaults.get(CONF_AIRPLAY_FALLBACK, True),
            ): selector.BooleanSelector(),
        }
    )


async def _async_probe(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Check the control port answers and read the device identity."""
    client = LPClient(host)
    try:
        await client.get_playing()
    finally:
        await client.close()
    try:
        return await hass.async_add_executor_job(discover, host)
    except LPError:
        # Identity is nice-to-have; the control port is what matters.
        return {}


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(host) and " " not in host
    return True


class LPConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a player by IP address, then optionally link its speaker plug."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._title: str = DEFAULT_NAME
        self._identity: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not _valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    identity = await _async_probe(self.hass, host)
                except LPError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error probing %s", host)
                    errors["base"] = "unknown"
                else:
                    unique_id = device_id(identity) or host
                    await self.async_set_unique_id(unique_id)
                    # Same player at a new address: just update the address.
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    self._host = host
                    self._identity = identity
                    self._title = (
                        user_input.get(CONF_NAME) or identity.get("devicename") or DEFAULT_NAME
                    )
                    return await self.async_step_power()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_power(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data={CONF_HOST: self._host, "identity": self._identity},
                options={
                    CONF_POWER_SWITCH: user_input.get(CONF_POWER_SWITCH),
                    CONF_AIRPLAY_FALLBACK: user_input[CONF_AIRPLAY_FALLBACK],
                },
            )
        return self.async_show_form(
            step_id="power",
            data_schema=_power_schema({}),
            description_placeholders={"name": self._title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the player's IP address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not _valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    identity = await _async_probe(self.hass, host)
                except LPError:
                    errors["base"] = "cannot_connect"
                else:
                    new_id = device_id(identity)
                    if new_id and entry.unique_id and new_id != entry.unique_id:
                        return self.async_abort(reason="different_device")
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_HOST: host,
                            "identity": identity or entry.data.get("identity", {}),
                        },
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LPOptionsFlow()


class LPOptionsFlow(OptionsFlow):
    """Change the linked power switch and the AirPlay fallback."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_POWER_SWITCH: user_input.get(CONF_POWER_SWITCH),
                    CONF_AIRPLAY_FALLBACK: user_input[CONF_AIRPLAY_FALLBACK],
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=_power_schema(dict(self.config_entry.options)),
        )
