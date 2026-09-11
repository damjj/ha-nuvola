from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    api, coordinator = entry.runtime_data
    async_add_entities([
        NuvolaStudentsSensor(coordinator, entry),
        NuvolaGradesSensor(coordinator, entry),
        NuvolaAbsencesSensor(coordinator, entry),
        NuvolaHomeworkSensor(coordinator, entry),
        NuvolaNotesSensor(coordinator, entry),
    ])


class BaseNuvolaSensor(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name


class NuvolaStudentsSensor(BaseNuvolaSensor, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "students", "Alunni")

    @property
    def native_value(self):
        return len(self.coordinator.data.get("students", []))


class NuvolaGradesSensor(BaseNuvolaSensor, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "grades", "Voti")

    @property
    def native_value(self):
        data = self.coordinator.data.get("grades", [])
        if isinstance(data, dict):
            for key in ("voti", "materie", "data"):
                if isinstance(data.get(key), list):
                    return len(data[key])
        return len(data) if isinstance(data, list) else 0


class NuvolaAbsencesSensor(BaseNuvolaSensor, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "absences", "Assenze")

    @property
    def native_value(self):
        data = self.coordinator.data.get("absences", [])
        if isinstance(data, dict):
            for key in ("assenze", "data"):
                if isinstance(data.get(key), list):
                    return len(data[key])
        return len(data) if isinstance(data, list) else 0


class NuvolaHomeworkSensor(BaseNuvolaSensor, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "homework", "Compiti")

    @property
    def native_value(self):
        data = self.coordinator.data.get("homework", [])
        return len(data) if isinstance(data, list) else 0


class NuvolaNotesSensor(BaseNuvolaSensor, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "notes", "Note")

    @property
    def native_value(self):
        data = self.coordinator.data.get("notes", {})
        if isinstance(data, dict):
            return sum(len(v) for v in data.values() if isinstance(v, list))
        return len(data) if isinstance(data, list) else 0
