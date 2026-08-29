"""Config and options flows.

> **Every step handler takes exactly one positional argument.** A two-parameter signature binds
> the submitted form data to the wrong name, leaves ``user_input`` permanently ``None``, and
> loops the form forever with no error shown. It is a silent, maddening failure and it is worth
> the reminder here.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .avpro.client import AvProClient, AvProConnectionError
from .avpro.http_decode import decode
from .avpro.protocol import StatusEndpoint
from .avpro.state import MatrixState, apply
from .const import (
    CONF_ALLOW_WRITES,
    CONF_POLLING_PROFILE,
    CONF_TRANSPORT,
    DEFAULT_ALLOW_WRITES,
    DEFAULT_POLLING_PROFILE,
    DEFAULT_TRANSPORT,
    DOMAIN,
    POLLING_PROFILES,
    TRANSPORT_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

#: Models this integration has been reasoned about. A unit outside the list is still accepted --
#: port counts are derived from what it reports, so a sibling model mostly works -- but it is
#: logged, because an unexpected identity is worth knowing about before a bug report.
_KNOWN_MODEL_PREFIX = "AC-MX"


def normalise_host(raw: str) -> str:
    """Accept what people actually paste.

    A user copying from their browser brings ``http://192.0.2.10/`` along with the scheme and the
    trailing slash. Storing that unchanged produces ``http://http://...`` at request time.
    """
    host = raw.strip()
    for scheme in ("http://", "https://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme) :]
    return host.split("/", 1)[0].strip()


class AvProValidationError(Exception):
    """Carries the translation key for the message the user should see."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def async_validate_host(hass: Any, host: str) -> MatrixState:
    """Confirm the host is an AVPro matrix and return what it says about itself.

    This is `test-before-configure`, and it is a real check rather than a reachability probe: the
    firmware answers 200 for endpoints that do not exist, so "the request succeeded" proves only
    that *something* is listening on port 80. The response has to parse as this endpoint's data
    before the host is accepted.
    """
    client = AvProClient(async_get_clientsession(hass), host)

    try:
        identity = await client.async_read(StatusEndpoint.WEB)
    except AvProConnectionError as err:
        raise AvProValidationError("cannot_connect") from err

    if not identity.ok:
        # A web server that is not this device: a router login page, a NAS, anything.
        raise AvProValidationError("not_avpro")

    # Routing establishes the port count, and the count decides how many names to expect.
    try:
        video = await client.async_read(StatusEndpoint.VIDEO)
    except AvProConnectionError as err:
        raise AvProValidationError("cannot_connect") from err

    state = apply(MatrixState(), decode(StatusEndpoint.VIDEO, video, port_count=4))
    if not any(state.video_routes):
        raise AvProValidationError("not_avpro")

    # Routing established the width, so the name count is now known.
    state = apply(state, decode(StatusEndpoint.WEB, identity, port_count=state.port_count))
    if state.model is None:
        # The identity body parsed but its arity was wrong, which on this device means a port
        # name contains '&'. Refusing beats guessing which field was split.
        raise AvProValidationError("unexpected_response")

    if not state.model.startswith(_KNOWN_MODEL_PREFIX):
        _LOGGER.warning(
            "Accepting an unrecognised model %r with %d ports; port counts are derived from the "
            "device, so this is expected to work, but please report it",
            state.model,
            state.port_count,
        )

    # Best-effort: a unit whose network body will not parse is still perfectly usable, it just
    # has to fall back to a host-derived unique id.
    try:
        network = await client.async_read(StatusEndpoint.NETWORK)
        state = apply(state, decode(StatusEndpoint.NETWORK, network, port_count=state.port_count))
    except AvProConnectionError:
        _LOGGER.debug("%s: network status unavailable; falling back to a host-derived id", host)

    return state


def _unique_id(state: MatrixState, host: str) -> str:
    """Prefer the MAC; fall back to the host.

    A working unit must never be refused because its network body surprised the parser, so the
    fallback exists. It is weaker -- the entry follows the address rather than the hardware -- so
    the MAC is used whenever it is there.
    """
    if state.mac:
        return dr.format_mac(state.mac)
    _LOGGER.warning("No MAC reported by %s; falling back to a host-derived unique id", host)
    return f"host-{host}"


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_TRANSPORT,
                default=current.get(CONF_TRANSPORT, DEFAULT_TRANSPORT),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(TRANSPORT_OPTIONS),
                    translation_key=CONF_TRANSPORT,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_POLLING_PROFILE,
                default=current.get(CONF_POLLING_PROFILE, DEFAULT_POLLING_PROFILE),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(POLLING_PROFILES),
                    translation_key=CONF_POLLING_PROFILE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_ALLOW_WRITES,
                default=current.get(CONF_ALLOW_WRITES, DEFAULT_ALLOW_WRITES),
            ): cv.boolean,
        }
    )


class AvProConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one matrix."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the host and prove it is a matrix before creating anything."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalise_host(user_input[CONF_HOST])
            try:
                state = await async_validate_host(self.hass, host)
            except AvProValidationError as err:
                errors["base"] = err.reason
            except Exception:
                _LOGGER.exception("Unexpected error validating %s", host)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_unique_id(state, host))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=state.model or "AVPro Edge",
                    data={CONF_HOST: host},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): cv.string}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a new address.

        Aborts loudly if the new address is a *different* matrix. Silently re-targeting an entry
        would repoint every entity -- and every automation built on them -- at another set of
        rooms.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalise_host(user_input[CONF_HOST])
            try:
                state = await async_validate_host(self.hass, host)
            except AvProValidationError as err:
                errors["base"] = err.reason
            except Exception:
                _LOGGER.exception("Unexpected error validating %s", host)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_unique_id(state, host))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST)): cv.string}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return AvProOptionsFlow()


class AvProOptionsFlow(OptionsFlow):
    """Poll cadence and the write switch.

    Applied without reloading the entry: reloading would drop every entity and rebuild it just to
    change a number, which on an installation driving wall panels is a visible blink.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
