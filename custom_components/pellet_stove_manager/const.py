"""Constants for Pellet Stove Manager."""

from __future__ import annotations

DOMAIN = "pellet_stove_manager"
EVENT = f"{DOMAIN}_event"

CONF_MODE = "mode"
CONF_CODE = "device_code"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_POLL = "poll_interval"
CONF_MIRROR_PREFIX = "mirror_prefix"
CONF_COMMANDS = "commands_enabled"

MODE_CLOUD = "cloud"
MODE_LOCAL = "local"
MODE_SIMULATOR = "simulator"
MODE_MIRROR = "mirror"
MODES = (MODE_MIRROR, MODE_CLOUD, MODE_LOCAL, MODE_SIMULATOR)

DEFAULT_POLL = 60
MIN_POLL = 30
DEFAULT_MIRROR_PREFIX = "salon_poele_a_granules"

UNAVAILABLE_AFTER = 3  # consecutive failed snapshots
RECHECK_DELAY = 5  # seconds, second reading after an odd snapshot

HEATING_STATES = ("ignition", "heating", "eco")
STATUS_OPTIONS = ("off", "ignition", "heating", "cleaning", "eco", "cooling", "alarm", "unknown")

# consumables and notifications (options)
CONF_OUTDOOR = "outdoor_entity"
CONF_HOUSE_OPEN = "house_open_entity"
CONF_NOTIFY = "notify_targets"
CONF_TANK_KG = "tank_kg"
CONF_BAG_KG = "bag_kg"
CONF_BAG_PRICE = "bag_price"
CONF_CLEANING_DEFAULT_H = "cleaning_default_hours"
CONF_CLEANING_REFILLS = "cleaning_refills"
BURNING_STATES = ("ignition", "heating", "eco")
