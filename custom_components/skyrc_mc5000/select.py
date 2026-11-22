"""Program pickers for each slot, and the charger's system settings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from skyrc_ble import Mc5000BatteryType, Mc5000Operation, Mc5000Settings
from skyrc_ble.models import MC5000_BEEP_LEVELS, MC5000_SCREEN_TIMEOUTS

from . import Mc5000ConfigEntry
from .const import SLOTS
from .entity import Mc5000Entity
from .program import operations_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for slot in SLOTS:
        entities.append(Mc5000ChemistrySelect(coordinator, slot))
        entities.append(Mc5000OperationSelect(coordinator, slot))
    entities.extend(Mc5000SettingSelect(coordinator, d) for d in SETTING_SELECTS)
    async_add_entities(entities)


BACKLIGHT = {"low": 1, "medium": 2, "high": 3}
TIMEOUTS = {("off" if m == 0 else f"{m}_min"): m for m in MC5000_SCREEN_TIMEOUTS}


@dataclass(frozen=True, kw_only=True)
class Mc5000SettingDescription(SelectEntityDescription):
    get_fn: Callable[[Mc5000Settings], str | None]
    set_fn: Callable[[str], dict[str, Any]]


def _beep(field: str) -> tuple[Callable, Callable]:
    def get(s: Mc5000Settings) -> str | None:
        value = getattr(s, field)
        return MC5000_BEEP_LEVELS[value] if 0 <= value < len(MC5000_BEEP_LEVELS) else None

    return get, lambda option: {field: MC5000_BEEP_LEVELS.index(option)}


_key_get, _key_set = _beep("keypress_beep")
_notify_get, _notify_set = _beep("notify_beep")

SETTING_SELECTS: tuple[Mc5000SettingDescription, ...] = (
    Mc5000SettingDescription(
        key="backlight", translation_key="backlight", options=list(BACKLIGHT),
        get_fn=lambda s: next((k for k, v in BACKLIGHT.items() if v == s.backlight), None),
        set_fn=lambda o: {"backlight": BACKLIGHT[o]},
    ),
    Mc5000SettingDescription(
        key="screen_timeout", translation_key="screen_timeout", options=list(TIMEOUTS),
        get_fn=lambda s: next((k for k, v in TIMEOUTS.items() if v == s.screen_timeout), None),
        set_fn=lambda o: {"screen_timeout": TIMEOUTS[o]},
    ),
    Mc5000SettingDescription(
        key="keypress_beep", translation_key="keypress_beep", options=list(MC5000_BEEP_LEVELS),
        get_fn=_key_get, set_fn=_key_set,
    ),
    Mc5000SettingDescription(
        key="notify_beep", translation_key="notify_beep", options=list(MC5000_BEEP_LEVELS),
        get_fn=_notify_get, set_fn=_notify_set,
    ),
    Mc5000SettingDescription(
        key="completion_tone", translation_key="completion_tone", options=["once", "repeat"],
        get_fn=lambda s: "repeat" if s.completion_repeat else "once",
        set_fn=lambda o: {"completion_repeat": o == "repeat"},
    ),
)


class Mc5000SettingSelect(Mc5000Entity, SelectEntity):
    """One of the charger's own system settings."""

    entity_description: Mc5000SettingDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, description: Mc5000SettingDescription) -> None:
        super().__init__(coordinator, f"setting_{description.key}")
        self.entity_description = description

    @property
    def available(self) -> bool:
        settings = self.coordinator.settings
        return (
            super().available
            and settings is not None
            and self.entity_description.get_fn(settings) is not None
        )

    @property
    def current_option(self) -> str | None:
        settings = self.coordinator.settings
        return None if settings is None else self.entity_description.get_fn(settings)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_settings(**self.entity_description.set_fn(option))


class _ProgramSelect(Mc5000Entity, SelectEntity, RestoreEntity):
    """Settings live in Home Assistant, so they stay editable while offline."""

    def __init__(self, coordinator, slot: int, key: str) -> None:
        super().__init__(coordinator, f"slot{slot}_program_{key}")
        self._slot = slot
        self._attr_translation_key = f"program_{key}"
        self._attr_translation_placeholders = {"slot": str(slot)}

    @property
    def available(self) -> bool:
        return True

    @property
    def program(self):
        return self.coordinator.programs[self._slot]


class Mc5000ChemistrySelect(_ProgramSelect):
    _attr_options = [b.name.lower() for b in Mc5000BatteryType]

    def __init__(self, coordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "chemistry")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self.program.set_battery_type(Mc5000BatteryType[last.state.upper()])

    @property
    def current_option(self) -> str:
        return self.program.battery_type.name.lower()

    async def async_select_option(self, option: str) -> None:
        self.program.set_battery_type(Mc5000BatteryType[option.upper()])
        # The operation list depends on the chemistry, so refresh every entity.
        self.coordinator.async_update_listeners()


class Mc5000OperationSelect(_ProgramSelect):
    def __init__(self, coordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "operation")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self.program.operation = Mc5000Operation[last.state.upper()]

    @property
    def options(self) -> list[str]:
        return [op.name.lower() for op in operations_for(self.program.battery_type)]

    @property
    def current_option(self) -> str:
        return self.program.operation.name.lower()

    async def async_select_option(self, option: str) -> None:
        self.program.operation = Mc5000Operation[option.upper()]
        self.async_write_ha_state()
