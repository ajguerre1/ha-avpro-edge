"""Actions: the two things that do not fit an entity.

``route_all`` sends every output to one input. It exists as an action rather than as four
``select_source`` calls because on HTTP it is genuinely one request instead of four, which on a
transport that serialises every call is a real difference, not a micro-optimisation.

``send_command`` is the escape hatch, and it is the reason this module needs care. It replaces the
vendor control-system driver's raw-command action, so nobody loses the ability to reach something
this integration has not modelled.

**What it deliberately cannot do.** The endpoint is an enumeration, never a free-form URL or an
arbitrary telnet line. That is not defensive tidiness -- it is the difference between "run a
command I did not anticipate" and "reconfigure the matrix's IP address from an automation".
Changing the address is a non-goal of this integration precisely because a wrong one is a site
visit, and an escape hatch that can reach ``NetSendCmd`` would quietly reintroduce it. Factory
reset is excluded for the same reason.

Registered in ``async_setup`` rather than in ``async_setup_entry``, per the ``action-setup``
quality-scale rule: an action should exist as soon as the integration is loaded, so an automation
referencing it does not fail validation merely because a config entry has not started yet.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .avpro.protocol import CommandEndpoint
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_ROUTE_ALL = "route_all"
SERVICE_SEND_COMMAND = "send_command"

ATTR_ENTRY = "config_entry_id"
ATTR_SOURCE = "source"
ATTR_ENDPOINT = "endpoint"
ATTR_COMMAND = "command"

#: Endpoints ``send_command`` may reach. Everything the matrix does that this integration models,
#: and nothing that reconfigures the device itself.
#:
#: ``NetSendCmd`` and ``NetDHCPSendCmd`` are absent by construction rather than by validation:
#: they are not in this mapping, so no input can select them. A misconfigured address on a matrix
#: in a wiring closet is a site visit, which is not a thing an automation should be able to cause.
ALLOWED_ENDPOINTS: dict[str, CommandEndpoint] = {
    "video": CommandEndpoint.VIDEO,
    "audio": CommandEndpoint.AUDIO,
    "system": CommandEndpoint.SYSTEM,
    "edid": CommandEndpoint.EDID,
    "tmds": CommandEndpoint.TMDS,
}

ROUTE_ALL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_SOURCE): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
    }
)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_ENDPOINT): vol.In(sorted(ALLOWED_ENDPOINTS)),
        # Alphanumeric only. The CGI interface takes the value as a query parameter, so anything
        # that could carry a separator has to be refused rather than escaped -- the device's own
        # parser is what would have to be trusted, and it has already been observed splitting on
        # unescaped '&' in its own responses.
        vol.Required(ATTR_COMMAND): vol.Match(r"^[A-Za-z0-9]{1,32}$"),
    }
)


def _runtime(hass: HomeAssistant, entry_id: str) -> Any:
    """The loaded runtime for an entry, or a user-facing error saying why not."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_entry",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register both actions once, for the integration as a whole."""

    async def _route_all(call: ServiceCall) -> None:
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        await runtime.coordinator.async_route_all(call.data[ATTR_SOURCE])

    async def _send_command(call: ServiceCall) -> ServiceResponse:
        """Send one command and hand back what the device said.

        The response matters more here than for most actions. This firmware answers an
        unsupported command with ``NO SUPPORT`` and a *200*, so without the raw body a user
        experimenting with an unmodelled command has no way to tell "it worked" from "it was
        politely ignored".
        """
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        endpoint = ALLOWED_ENDPOINTS[call.data[ATTR_ENDPOINT]]
        command = call.data[ATTR_COMMAND]

        _LOGGER.debug("send_command: %s -> %s", endpoint.value, command)
        try:
            result = await runtime.client.async_command(endpoint, command)
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        return {
            "supported": result.supported,
            "outcome": result.outcome.value,
            # Verbatim. Trimming or parsing it would defeat the purpose: the caller is here
            # because the integration did not model whatever they are doing.
            "response": result.raw,
        }

    hass.services.async_register(DOMAIN, SERVICE_ROUTE_ALL, _route_all, schema=ROUTE_ALL_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
