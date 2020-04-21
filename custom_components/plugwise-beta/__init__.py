"""Plugwise platform for Home Assistant Core."""

import asyncio
import logging
from datetime import timedelta
from typing import Optional
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from Plugwise_Smile.Smile import Smile

from .const import DOMAIN

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

_LOGGER = logging.getLogger(__name__)

SENSOR = ["sensor"]
CLIMATE = ["binary_sensor", "climate", "sensor", "switch"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Plugwise platform."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plugwise Smiles from a config entry."""
    websession = async_get_clientsession(hass, verify_ssl=False)
    api = Smile(
        host=entry.data.get("host"),
        password=entry.data.get("password"),
        websession=websession,
    )

    await api.connect()

    if api.smile_type == "power":
        update_interval = timedelta(seconds=10)
    else:
        update_interval = timedelta(seconds=60)

    api.get_all_devices()

    _LOGGER.debug("Plugwise async update interval %s", update_interval)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "updater": SmileDataUpdater(
            hass, "device", entry.entry_id, api, "full_update_device", update_interval
        ),
    }

    _LOGGER.debug("Plugwise gateway is %s", api.gateway_id)
    device_registry = await dr.async_get_registry(hass)
    result = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, api.gateway_id)},
        manufacturer="Plugwise",
        name=entry.title,
        model=f"Smile {api.smile_name}",
        sw_version=api.smile_version[0],
    )
    _LOGGER.debug("Plugwise device registry  %s", result)

    single_master_thermostat = api.single_master_thermostat()
    _LOGGER.debug("Single master thermostat = %s", single_master_thermostat)
    if single_master_thermostat is None:
        PLATFORMS = SENSOR
    else:
        PLATFORMS = CLIMATE

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    async def async_refresh_all(_):
        """Refresh all Smile data."""
        for info in hass.data[DOMAIN].values():
            await info["updater"].async_refresh_all()

    hass.services.async_register(DOMAIN, "update", async_refresh_all)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class SmileDataUpdater:
    """Data storage for single Smile API endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        data_type: str,
        config_entry_id: str,
        api: Smile,
        update_method: str,
        update_interval: timedelta,
    ):
        """Initialize global data updater."""
        self.hass = hass
        self.data_type = data_type
        self.config_entry_id = config_entry_id
        self.api = api
        self.update_method = update_method
        self.update_interval = update_interval
        self.listeners = []
        self._unsub_interval = None

    @callback
    def async_add_listener(self, update_callback):
        """Listen for data updates."""
        if not self.listeners:
            self._unsub_interval = async_track_time_interval(
                self.hass, self.async_refresh_all, self.update_interval
            )

        self.listeners.append(update_callback)

    @callback
    def async_remove_listener(self, update_callback):
        """Remove data update."""
        self.listeners.remove(update_callback)

        if not self.listeners:
            self._unsub_interval()
            self._unsub_interval = None

    async def async_refresh_all(self, _now: Optional[int] = None) -> None:
        """Time to update."""
        _LOGGER.debug("Plugwise Smile updating with interval: %s", self.update_interval)
        if not self.listeners:
            _LOGGER.error("Plugwise Smile has no listeners, not updating")
            return

        _LOGGER.debug("Plugwise Smile updating data using: %s", self.update_method)

        await self.api.full_update_device()

        for update_callback in self.listeners:
            update_callback()
