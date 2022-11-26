"""Support for Tuya Climate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tuya_iot import TuyaDevice, TuyaDeviceManager

from homeassistant.components.climate import (
    PRESET_ECO,
    PRESET_NONE,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_ON,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import TEMP_CELSIUS, TEMP_FAHRENHEIT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HomeAssistantTuyaData
from .base import IntegerTypeData, TuyaEntity

from .const import DOMAIN, TUYA_DISCOVERY_NEW, DPCode, DPType, CONF_INSTRUCTIONS_TYPE, LOGGER

TUYA_HVAC_TO_HA = {
    "auto": HVACMode.HEAT_COOL,
    "cold": HVACMode.COOL,
    "freeze": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "hot": HVACMode.HEAT,
    "manual": HVACMode.HEAT_COOL,
    "wet": HVACMode.DRY,
    "wind": HVACMode.FAN_ONLY,
}


@dataclass
class TuyaClimateSensorDescriptionMixin:
    """Define an entity description mixin for climate entities."""

    switch_only_hvac_mode: HVACMode


@dataclass
class TuyaClimateEntityDescription(
    ClimateEntityDescription, TuyaClimateSensorDescriptionMixin
):
    """Describe an Tuya climate entity."""


CLIMATE_DESCRIPTIONS: dict[str, TuyaClimateEntityDescription] = {
    # Air conditioner
    # https://developer.tuya.com/en/docs/iot/categorykt?id=Kaiuz0z71ov2n
    "kt": TuyaClimateEntityDescription(
        key="kt",
        switch_only_hvac_mode=HVACMode.COOL,
    ),
    # Heater
    # https://developer.tuya.com/en/docs/iot/f?id=K9gf46epy4j82
    "qn": TuyaClimateEntityDescription(
        key="qn",
        switch_only_hvac_mode=HVACMode.HEAT,
    ),
    # Heater
    # https://developer.tuya.com/en/docs/iot/categoryrs?id=Kaiuz0nfferyx
    "rs": TuyaClimateEntityDescription(
        key="rs",
        switch_only_hvac_mode=HVACMode.HEAT,
    ),
    # Thermostat
    # https://developer.tuya.com/en/docs/iot/f?id=K9gf45ld5l0t9
    "wk": TuyaClimateEntityDescription(
        key="wk",
        switch_only_hvac_mode=HVACMode.HEAT_COOL,
    ),
    # Thermostatic Radiator Valve
    # Not documented
    "wkf": TuyaClimateEntityDescription(
        key="wkf",
        switch_only_hvac_mode=HVACMode.HEAT,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Tuya climate dynamically through Tuya discovery."""
    hass_data: HomeAssistantTuyaData = hass.data[DOMAIN][entry.entry_id]

    @callback
    def async_discover_device(device_ids: list[str]) -> None:
        """Discover and add a discovered Tuya climate."""
        entities: list[TuyaClimateEntity] = []
        for device_id in device_ids:
            device = hass_data.device_manager.device_map[device_id]
            if device and device.category in CLIMATE_DESCRIPTIONS:
                entities.append(
                    TuyaClimateEntity(
                        device,
                        hass_data.device_manager,
                        CLIMATE_DESCRIPTIONS[device.category],
                        instruction_type=entry.data[CONF_INSTRUCTIONS_TYPE],
                    )
                )
        async_add_entities(entities)

    async_discover_device([*hass_data.device_manager.device_map])

    entry.async_on_unload(
        async_dispatcher_connect(hass, TUYA_DISCOVERY_NEW, async_discover_device)
    )


class TuyaClimateEntity(TuyaEntity, ClimateEntity):
    """Tuya Climate Device."""

    _current_humidity: IntegerTypeData | None = None
    _current_temperature: IntegerTypeData | None = None
    _hvac_to_tuya: dict[str, str]
    _set_humidity: IntegerTypeData | None = None
    _set_temperature: IntegerTypeData | None = None
    _attr_preset_mode: str = PRESET_NONE
    entity_description: TuyaClimateEntityDescription

    def __init__(
        self,
        device: TuyaDevice,
        device_manager: TuyaDeviceManager,
        description: TuyaClimateEntityDescription,
        instruction_type: str = "Standard",
    ) -> None:
        """Determine which values to use."""
        self._attr_target_temperature_step = 1.0
        self.entity_description = description
        self._attr_preset_mode = PRESET_NONE
        LOGGER.debug("Created device with instruction type %s", instruction_type)

        super().__init__(device, device_manager, instruction_type=instruction_type)
        # If both temperature values for celsius and fahrenheit are present,
        # use whatever the device is set to, with a fallback to celsius.
        prefered_temperature_unit = None
        if all(
            dpcode in device.status
            for dpcode in (
                self._get_right_dpcode(DPCode.TEMP_CURRENT),
                self._get_right_dpcode(DPCode.TEMP_CURRENT_F),
            )
        ) or all(
            dpcode in device.status
            for dpcode in (
                self._get_right_dpcode(DPCode.TEMP_SET),
                self._get_right_dpcode(DPCode.TEMP_SET_F),
            )
        ):
            prefered_temperature_unit = TEMP_CELSIUS
            if any(
                "f" in device.status[dpcode].lower()
                for dpcode in (
                    self._get_right_dpcode(DPCode.C_F),
                    self._get_right_dpcode(DPCode.TEMP_UNIT_CONVERT),
                )
                if isinstance(device.status.get(dpcode), str)
            ):
                prefered_temperature_unit = TEMP_FAHRENHEIT

        # Default to Celsius
        self._attr_temperature_unit = TEMP_CELSIUS

        # Figure out current temperature, use preferred unit or what is available
        celsius_type = self.find_dpcode(
            (
                self._get_right_dpcode(DPCode.TEMP_CURRENT),
                self._get_right_dpcode(DPCode.UPPER_TEMP),
            ),
            dptype=DPType.INTEGER,
        )
        farhenheit_type = self.find_dpcode(
            (
                self._get_right_dpcode(DPCode.TEMP_CURRENT_F),
                self._get_right_dpcode(DPCode.UPPER_TEMP_F),
            ),
            dptype=DPType.INTEGER,
        )
        if farhenheit_type and (
            prefered_temperature_unit == TEMP_FAHRENHEIT
            or (prefered_temperature_unit == TEMP_CELSIUS and not celsius_type)
        ):
            self._attr_temperature_unit = TEMP_FAHRENHEIT
            self._current_temperature = farhenheit_type
        elif celsius_type:
            self._attr_temperature_unit = TEMP_CELSIUS
            self._current_temperature = celsius_type

        # Figure out setting temperature, use preferred unit or what is available
        celsius_type = self.find_dpcode(
            self._get_right_dpcode(DPCode.TEMP_SET),
            dptype=DPType.INTEGER,
            prefer_function=True,
        )
        farhenheit_type = self.find_dpcode(
            self._get_right_dpcode(DPCode.TEMP_SET_F),
            dptype=DPType.INTEGER,
            prefer_function=True,
        )
        if farhenheit_type and (
            prefered_temperature_unit == TEMP_FAHRENHEIT
            or (prefered_temperature_unit == TEMP_CELSIUS and not celsius_type)
        ):
            self._set_temperature = farhenheit_type
        elif celsius_type:
            self._set_temperature = celsius_type

        # Get integer type data for the dpcode to set temperature, use
        # it to define min, max & step temperatures
        if self._set_temperature:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_max_temp = self._set_temperature.max_scaled
            self._attr_min_temp = self._set_temperature.min_scaled
            self._attr_target_temperature_step = self._set_temperature.step_scaled

        # Determine HVAC modes
        self._attr_hvac_modes: list[str] = []
        self._hvac_to_tuya = {}
        if enum_type := self.find_dpcode(
            self._get_right_dpcode(DPCode.MODE),
            dptype=DPType.ENUM,
            prefer_function=True,
        ):
            self._attr_hvac_modes = [HVACMode.OFF]
            unknown_hvac_modes: list[str] = []
            for tuya_mode in enum_type.range:
                if tuya_mode in TUYA_HVAC_TO_HA:
                    ha_mode = TUYA_HVAC_TO_HA[tuya_mode]
                    self._hvac_to_tuya[ha_mode] = tuya_mode
                    self._attr_hvac_modes.append(ha_mode)
                else:
                    unknown_hvac_modes.append(tuya_mode)

            if unknown_hvac_modes:  # Tuya modes are presets instead of hvac_modes
                self._attr_hvac_modes.append(description.switch_only_hvac_mode)
                self._attr_preset_modes = unknown_hvac_modes
                self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
        elif self.find_dpcode(self._get_right_dpcode(DPCode.SWITCH), prefer_function=True):
            self._attr_hvac_modes = [
                HVACMode.OFF,
                description.switch_only_hvac_mode,
            ]

        LOGGER.debug("the hvac modes are %s", self._attr_hvac_modes)

        # Determine dpcode to use for setting the humidity
        if int_type := self.find_dpcode(
            self._get_right_dpcode(DPCode.HUMIDITY_SET),
            dptype=DPType.INTEGER,
            prefer_function=True,
        ):
            self._attr_supported_features |= ClimateEntityFeature.TARGET_HUMIDITY
            self._set_humidity = int_type
            self._attr_min_humidity = int(int_type.min_scaled)
            self._attr_max_humidity = int(int_type.max_scaled)

        # Determine dpcode to use for getting the current humidity
        self._current_humidity = self.find_dpcode(
            self._get_right_dpcode(DPCode.HUMIDITY_CURRENT), dptype=DPType.INTEGER
        )

        # Determine fan modes
        if enum_type := self.find_dpcode(
            (
                self._get_right_dpcode(DPCode.FAN_SPEED_ENUM),
                self._get_right_dpcode(DPCode.WINDSPEED),
            ),
            dptype=DPType.ENUM,
            prefer_function=True,
        ):
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = enum_type.range

        # Determine swing modes
        if self.find_dpcode(
            (
                self._get_right_dpcode(DPCode.SHAKE),
                self._get_right_dpcode(DPCode.SWING),
                self._get_right_dpcode(DPCode.SWITCH_HORIZONTAL),
                self._get_right_dpcode(DPCode.SWITCH_VERTICAL),
            ),
            prefer_function=True,
        ):
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = [SWING_OFF]
            if self.find_dpcode((DPCode.SHAKE, DPCode.SWING), prefer_function=True):
                self._attr_swing_modes.append(SWING_ON)

            if self.find_dpcode(DPCode.SWITCH_HORIZONTAL, prefer_function=True):
                self._attr_swing_modes.append(SWING_HORIZONTAL)

            if self.find_dpcode(DPCode.SWITCH_VERTICAL, prefer_function=True):
                self._attr_swing_modes.append(SWING_VERTICAL)

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        # Log unknown modes
        if enum_type := self.find_dpcode(
            self._get_right_dpcode(DPCode.MODE),
            dptype=DPType.ENUM,
            prefer_function=True,
        ):
            LOGGER.debug(
                "in registration instruction is %s and description %s",
                self._instruction_type,
                self.entity_description.key,
            )
            if self._instruction_type == "DP Instructions":
                if self.entity_description.key == "wk":
                    TUYA_HVAC_TO_HA["0"] = HVACMode.AUTO
                    TUYA_HVAC_TO_HA["1"] = HVACMode.HEAT_COOL
            for tuya_mode in enum_type.range:
                if tuya_mode not in TUYA_HVAC_TO_HA:
                    LOGGER.warning(
                        "Unknown HVAC mode '%s' for device %s; assuming it as off",
                        tuya_mode,
                        self.device.name,
                    )


    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        commands = [
            {
                "code": self._get_right_dpcode(DPCode.SWITCH),
                "value": hvac_mode != HVACMode.OFF,
            }
        ]
        if hvac_mode in self._hvac_to_tuya:
            commands.append(
                {
                    "code": self._get_right_dpcode(DPCode.MODE),
                    "value": self._hvac_to_tuya[hvac_mode],
                }
            )
        self._send_command(commands)

    def set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        commands = [{"code": DPCode.MODE, "value": preset_mode}]
        self._send_command(commands)

    def set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        self._send_command(
            [{"code": self._get_right_dpcode(DPCode.FAN_SPEED_ENUM), "value": fan_mode}]
        )

    def set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        if self._set_humidity is None:
            raise RuntimeError(
                "Cannot set humidity, device doesn't provide methods to set it"
            )

        self._send_command(
            [
                {
                    "code": self._set_humidity.dpcode,
                    "value": self._set_humidity.scale_value_back(humidity),
                }
            ]
        )

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        # The API accepts these all at once and will ignore the codes
        # that don't apply to the device being controlled.
        self._send_command(
            [
                {
                    "code": self._get_right_dpcode(DPCode.SHAKE),
                    "value": swing_mode == SWING_ON,
                },
                {
                    "code": self._get_right_dpcode(DPCode.SWING),
                    "value": swing_mode == SWING_ON,
                },
                {
                    "code": self._get_right_dpcode(DPCode.SWITCH_VERTICAL),
                    "value": swing_mode in (SWING_BOTH, SWING_VERTICAL),
                },
                {
                    "code": self._get_right_dpcode(DPCode.SWITCH_HORIZONTAL),
                    "value": swing_mode in (SWING_BOTH, SWING_HORIZONTAL),
                },
            ]
        )

    def set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if self._set_temperature is None:
            raise RuntimeError(
                "Cannot set target temperature, device doesn't provide methods to set it"
            )

        self._send_command(
            [
                {
                    "code": self._set_temperature.dpcode,
                    "value": round(
                        self._set_temperature.scale_value_back(kwargs["temperature"])
                    ),
                }
            ]
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if self._current_temperature is None:
            return None

        temperature = self.device.status.get(self._current_temperature.dpcode)
        if temperature is None:
            return None

        if self._current_temperature.scale == 0 and self._current_temperature.step != 1:
            # The current temperature can have a scale of 0 or 1 and is used for
            # rounding, Home Assistant doesn't need to round but we will always
            # need to divide the value by base (default 10) in case of 0 as scale.
            # https://developer.tuya.com/en/docs/iot/shift-temperature-scale-follow-the-setting-of-app-account-center?id=Ka9qo7so58efq#title-7-Round%20values
            temperature = temperature / self._current_temperature.base_value

        return self._current_temperature.scale_value(temperature)

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        if self._current_humidity is None:
            return None

        humidity = self.device.status.get(self._current_humidity.dpcode)
        if humidity is None:
            return None

        return round(self._current_humidity.scale_value(humidity))

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature currently set to be reached."""
        if self._set_temperature is None:
            return None

        temperature = self.device.status.get(self._set_temperature.dpcode)
        if temperature is None:
            return None

        return self._set_temperature.scale_value(temperature)

    @property
    def target_humidity(self) -> int | None:
        """Return the humidity currently set to be reached."""
        if self._set_humidity is None:
            return None

        humidity = self.device.status.get(self._set_humidity.dpcode)
        if humidity is None:
            return None

        return round(self._set_humidity.scale_value(humidity))

    @property
    def hvac_mode(self) -> HVACMode:
        """Return hvac mode."""
        # If the switch off, hvac mode is off as well. Unless the switch
        # the switch is on or doesn't exists of course...
        if not self.device.status.get(self._get_right_dpcode(DPCode.SWITCH), True):
            return HVACMode.OFF

        if self._get_right_dpcode(DPCode.MODE) not in self.device.function:
            if self.device.status.get(self._get_right_dpcode(DPCode.SWITCH), False):
                return self.entity_description.switch_only_hvac_mode
            return HVACMode.OFF

        if (
            mode := self.device.status.get(self._get_right_dpcode(DPCode.MODE))
        ) is not None and mode in TUYA_HVAC_TO_HA:
            return TUYA_HVAC_TO_HA[mode]

        # If the switch is on, and the mode does not match any hvac mode.
        if self.device.status.get(DPCode.SWITCH, False):
            return self.entity_description.switch_only_hvac_mode

        return HVACMode.OFF

    @property
    def preset_mode(self) -> str | None:
        """Return preset mode."""
        if DPCode.MODE not in self.device.function:
            return None

        mode = self.device.status.get(DPCode.MODE)
        if mode in TUYA_HVAC_TO_HA:
            return None

        return mode

    @property
    def fan_mode(self) -> str | None:
        """Return fan mode."""
        return self.device.status.get(self._get_right_dpcode(DPCode.FAN_SPEED_ENUM))

    @property
    def swing_mode(self) -> str:
        """Return swing mode."""
        if any(
            self.device.status.get(dpcode)
            for dpcode in (
                self._get_right_dpcode(DPCode.SHAKE),
                self._get_right_dpcode(DPCode.SWING),
            )
        ):
            return SWING_ON

        horizontal = self.device.status.get(
            self._get_right_dpcode(DPCode.SWITCH_HORIZONTAL)
        )
        vertical = self.device.status.get(
            self._get_right_dpcode(DPCode.SWITCH_VERTICAL)
        )
        if horizontal and vertical:
            return SWING_BOTH
        if horizontal:
            return SWING_HORIZONTAL
        if vertical:
            return SWING_VERTICAL

        return SWING_OFF

    def turn_on(self) -> None:
        """Turn the device on, retaining current HVAC (if supported)."""
        if self._get_right_dpcode(DPCode.SWITCH) in self.device.function:
            self._send_command(
                [{"code": self._get_right_dpcode(DPCode.SWITCH), "value": True}]
            )
            return

        # Fake turn on
        for mode in (HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL):
            if mode not in self.hvac_modes:
                continue
            self.set_hvac_mode(mode)
            break

    def turn_off(self) -> None:
        """Turn the device on, retaining current HVAC (if supported)."""
        if self._get_right_dpcode(DPCode.SWITCH) in self.device.function:
            self._send_command(
                [{"code": self._get_right_dpcode(DPCode.SWITCH), "value": False}]
            )
            return

        # Fake turn off
        if HVACMode.OFF in self.hvac_modes:
            self.set_hvac_mode(HVACMode.OFF)

    @property
    def preset_modes(self) -> list[str] | None:
        """Return available preset modes."""
        return [PRESET_NONE, PRESET_ECO]

    @property
    def preset_mode(self) -> str | None:
        """Return preset mode eco if on, off otherwise."""
        eco_mode = self.device.status.get(self._get_right_dpcode(DPCode.ECO))
        return PRESET_ECO if eco_mode else PRESET_NONE

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set Eco mode on or off."""
        if self.preset_modes is None or preset_mode not in self.preset_modes:
            return
        LOGGER.debug("called set preset with %s", preset_mode)
        self._attr_preset_mode = preset_mode
        send_value = False
        if preset_mode == PRESET_ECO:
            send_value = True
        LOGGER.debug("sending command for preset with value %s", send_value)
        self._send_command(
            [
                {
                    "code": self._get_right_dpcode(DPCode.ECO),
                    "value": send_value,
                }
            ]
        )
