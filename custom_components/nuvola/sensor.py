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
        NuvolaBulletinSensor(coordinator, entry),
        NuvolaBoardsSensor(coordinator, entry),
        NuvolaCircolariSensor(coordinator, entry),
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


class NuvolaBulletinSensor(BaseNuvolaSensor, SensorEntity):
    """Monitor documents published in Nuvola bulletin boards."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "bulletin", "Bacheca")

    @property
    def native_value(self):
        documents = self.coordinator.data.get("bulletin_documents", [])
        return len(documents) if isinstance(documents, list) else 0

    @property
    def extra_state_attributes(self):
        documents = self.coordinator.data.get("bulletin_documents", [])
        if not isinstance(documents, list):
            documents = []

        def sort_key(item):
            return str(item.get("dataPubblicazione") or "")

        ordered = sorted(documents, key=sort_key, reverse=True)
        latest = ordered[0] if ordered else {}

        unread = [
            item for item in documents
            if item.get("isRead") is False
            or item.get("documentoBachecaLetto") is False
            or (isinstance(item.get("metadata"), dict) and item["metadata"].get("isRead") is False)
        ]

        attachments = []
        for attachment in latest.get("allegati") or []:
            if not isinstance(attachment, dict):
                continue
            attachment_id = attachment.get("id")
            item = {
                "id": attachment_id,
                "nome": attachment.get("nome"),
                "mime_type": attachment.get("mimeType"),
            }
            if attachment_id:
                student = self.coordinator.data.get("student") or {}
                student_id = student.get("id") or student.get("id_alunno")
                if student_id is not None:
                    item["preview_url"] = (
                        f"https://nuvola.madisoft.it/api-studente/v1/alunno/{student_id}/"
                        f"file-preview/{attachment_id}?contextAlunno={student_id}"
                    )
            attachments.append(item)

        attrs = {
            "non_lette": len(unread),
            "ultimo_id": latest.get("id"),
            "ultima_pubblicazione": latest.get("dataPubblicazione"),
            "ultima_archiviazione": latest.get("dataArchiviazione"),
            "ultimo_oggetto": latest.get("oggetto"),
            "ultimo_letto": latest.get("isRead", latest.get("documentoBachecaLetto")),
            "ultima_richiesta_adesione": latest.get("adesioneRichiesta"),
            "ultima_scadenza_adesione": latest.get("dataScadenzaAdesione"),
            "ultimi_allegati": attachments,
        }
        return attrs


class NuvolaBoardsSensor(BaseNuvolaSensor, SensorEntity):
    """Expose the number and names of available digital bulletin boards."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "boards", "Bacheche digitali")

    @property
    def native_value(self):
        boards = self.coordinator.data.get("bulletin_boards", [])
        return len(boards) if isinstance(boards, list) else 0

    @property
    def extra_state_attributes(self):
        boards = self.coordinator.data.get("bulletin_boards", [])
        if not isinstance(boards, list):
            boards = []
        return {
            "bacheche": [
                {"id": board.get("id"), "nome": board.get("nome")}
                for board in boards
                if isinstance(board, dict)
            ]
        }


class NuvolaCircolariSensor(BaseNuvolaSensor, SensorEntity):
    """Expose the dynamically discovered CIRCOLARI board."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "circolari", "Circolari")

    @property
    def native_value(self):
        documents = self.coordinator.data.get("circolari_documents", [])
        return len(documents) if isinstance(documents, list) else 0

    @property
    def extra_state_attributes(self):
        board = self.coordinator.data.get("circolari_board")
        documents = self.coordinator.data.get("circolari_documents", [])
        if not isinstance(documents, list):
            documents = []

        ordered = sorted(
            documents,
            key=lambda item: str(item.get("dataPubblicazione") or ""),
            reverse=True,
        )
        latest = ordered[0] if ordered else {}
        unread = [
            item for item in documents
            if item.get("isRead") is False
            or item.get("documentoBachecaLetto") is False
            or (
                isinstance(item.get("metadata"), dict)
                and item["metadata"].get("isRead") is False
            )
        ]

        attrs = {
            "presente": isinstance(board, dict),
            "id": board.get("id") if isinstance(board, dict) else None,
            "nome": board.get("nome") if isinstance(board, dict) else None,
            "numero_documenti": len(documents),
            "non_lette": len(unread),
            "ultimo_id": latest.get("id"),
            "ultima_pubblicazione": latest.get("dataPubblicazione"),
            "ultimo_oggetto": latest.get("oggetto"),
            "ultimo_letto": latest.get("isRead", latest.get("documentoBachecaLetto")),
            "ultime_circolari": ordered[:10],
        }
        if isinstance(board, dict):
            attrs.update({
                "testo": board.get("testo"),
                "voci": board.get("voci"),
                "nascondi_allegati_archiviati": board.get(
                    "nascondiAllegatiDeiDocumentiArchiviati"
                ),
                "mostra_testo_solo_pagina_iniziale": board.get(
                    "mostraTestoSoloInPaginaIniziale"
                ),
            })
        return attrs
