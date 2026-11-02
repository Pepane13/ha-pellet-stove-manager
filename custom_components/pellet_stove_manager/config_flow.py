"""Config flow. The device code is a credential: stored in the config entry only, never logged."""

from __future__ import annotations

import hashlib
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_CODE,
    CONF_HOST,
    CONF_MIRROR_PREFIX,
    CONF_MODE,
    CONF_POLL,
    CONF_PORT,
    DEFAULT_MIRROR_PREFIX,
    DEFAULT_POLL,
    DOMAIN,
    MIN_POLL,
    MODE_CLOUD,
    MODE_LOCAL,
    MODE_MIRROR,
    MODES,
)
from .duepi.client import DuepiClient
from .duepi.transport import DuepiTransport


class PelletStoveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._mode = MODE_MIRROR
        self._name = "Poêle à granulés"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            self._name = user_input["name"]
            if self._mode == MODE_MIRROR:
                return await self.async_step_mirror()
            if self._mode in (MODE_CLOUD, MODE_LOCAL):
                return await self.async_step_connection()
            return self.async_create_entry(title=self._name, data={CONF_MODE: self._mode})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=self._name): str,
                    vol.Required(CONF_MODE, default=MODE_MIRROR): vol.In(list(MODES)),
                }
            ),
        )

    async def async_step_mirror(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            prefix = user_input[CONF_MIRROR_PREFIX].strip()
            if self.hass.states.get(f"select.{prefix}_stove_status") is None:
                errors["base"] = "mirror_not_found"
            else:
                return self.async_create_entry(
                    title=self._name, data={CONF_MODE: MODE_MIRROR, CONF_MIRROR_PREFIX: prefix}
                )
        return self.async_show_form(
            step_id="mirror",
            data_schema=vol.Schema({vol.Required(CONF_MIRROR_PREFIX, default=DEFAULT_MIRROR_PREFIX): str}),
            errors=errors,
        )

    async def async_step_connection(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        cloud = self._mode == MODE_CLOUD
        if user_input is not None:
            data = {CONF_MODE: self._mode, **user_input}
            transport = DuepiTransport(
                self._mode,
                host=data.get(CONF_HOST) or None,
                port=data.get(CONF_PORT) or None,
                code=data.get(CONF_CODE),
            )
            try:
                await DuepiClient(transport).read("status")  # read-only test
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            finally:
                await transport.close()
            if not errors:
                if cloud:
                    code = data[CONF_CODE].strip().rstrip("#")
                    await self.async_set_unique_id("cloud_" + hashlib.sha256(code.encode()).hexdigest()[:16])
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=self._name, data=data)
        schema = (
            {vol.Required(CONF_CODE): str}
            if cloud
            else {vol.Required(CONF_HOST): str, vol.Optional(CONF_PORT, default=23): int}
        )
        return self.async_show_form(step_id="connection", data_schema=vol.Schema(schema), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return PelletStoveOptionsFlow()


class PelletStoveOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL, default=current.get(CONF_POLL, DEFAULT_POLL)): vol.All(
                        int, vol.Range(min=MIN_POLL, max=900)
                    )
                }
            ),
        )
