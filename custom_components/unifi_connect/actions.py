"""Dynamic action ID resolution for UniFi Connect devices.

Firmware updates can change action UUIDs. This module resolves action IDs
at runtime from the device's type.category.supportedActions or
type.supportedActions data, falling back to hardcoded constants.
"""

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)


def get_supported_actions(device: dict) -> dict[str, dict]:
    """Build a lookup map of action_name -> {id, args} from device data.

    Checks type.category.supportedActions first (firmware 1.6.4+),
    then type.supportedActions (firmware 1.5.x), merging both.
    """
    actions: dict[str, dict] = {}

    # Source 1: type.supportedActions (older firmware)
    type_data = device.get("type", {})
    if isinstance(type_data, dict):
        for action in type_data.get("supportedActions", []):
            if isinstance(action, dict) and "name" in action:
                actions[action["name"]] = action

        # Source 2: type.category.supportedActions (firmware 1.6.4+)
        # These take precedence over type-level actions
        category = type_data.get("category", {})
        if isinstance(category, dict):
            for action in category.get("supportedActions", []):
                if isinstance(action, dict) and "name" in action:
                    actions[action["name"]] = action

    return actions


def resolve_action_id(
    device: dict, action_name: str, fallback_id: Optional[str] = None
) -> str:
    """Resolve the current action UUID for a given action name.

    Args:
        device: Full device dict from coordinator data.
        action_name: The action name (e.g., "set_max_output_amp").
        fallback_id: Hardcoded UUID to use if dynamic lookup fails.

    Returns:
        The action UUID string.
    """
    actions = get_supported_actions(device)
    action = actions.get(action_name)
    if action and "id" in action:
        resolved_id = action["id"]
        if fallback_id and resolved_id != fallback_id:
            _LOGGER.debug(
                "Action '%s' UUID resolved dynamically: %s (hardcoded was %s)",
                action_name,
                resolved_id,
                fallback_id,
            )
        return resolved_id

    if fallback_id:
        _LOGGER.warning(
            "Could not resolve action '%s' from device data, using fallback UUID %s",
            action_name,
            fallback_id,
        )
        return fallback_id

    _LOGGER.error("No action ID found for '%s' and no fallback provided", action_name)
    return ""


def get_action_arg_key(device: dict, action_name: str) -> Optional[str]:
    """Get the expected argument key name for an action from its JSON schema.

    For example, firmware 1.6.4 expects {"maxOutput": N} for set_max_output_amp,
    while older firmware used {"value": N}.

    Returns the first required property name, or None if the action has no args.
    """
    actions = get_supported_actions(device)
    action = actions.get(action_name)
    if not action or "args" not in action:
        return None

    args_schema = action["args"]
    if not isinstance(args_schema, dict):
        return None

    # Use the first required property, or first property if no required list
    required = args_schema.get("required", [])
    if required:
        return required[0]

    properties = args_schema.get("properties", {})
    if properties:
        return next(iter(properties))

    return None
