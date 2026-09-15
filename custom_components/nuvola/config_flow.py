from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import NuvolaAPI, NuvolaAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            api = NuvolaAPI(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                students = await api.students()
            except NuvolaAuthError as err:
                _LOGGER.error(
                    "Nuvola authentication rejected during setup: %s",
                    str(err) or "no diagnostic message returned",
                )
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error while configuring Nuvola")
                errors["base"] = "unknown"
            else:
                _LOGGER.info(
                    "Nuvola authentication successful; %d students found",
                    len(students),
                )
                await api.close()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input,
                )
            await api.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
            }),
            errors=errors,
        )
