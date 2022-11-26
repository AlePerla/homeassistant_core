"""Tuya device specific settings."""

# A dictionary keyed on product_id with device specific
# base used for scaling of Integer values. Default
# value is 10 according to:
# https://developer.tuya.com/en/docs/iot/datatypedescription?id=K9i5ql2jo7j1k
DEVICE_SPECIFIC_INT_BASE: dict[str, dict[str, int]] = {
    # BHT-002 Thermostat
    "IAYz2WK1th0cMLmL": {"base_value": 2, "base_step": 10}
    # Add more devices below
}
