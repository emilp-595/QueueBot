
STRONGLY_REQUIRED_FIELDS = [
    ("token", str),
    ("is_production", bool),
    ("lounge", str),
    ("lounge_description", str),
    ("guild_id", int),
    ("queue_join_channel", int),
    ("queue_sub_channel", int),
    ("queue_list_channel", int),
    ("queue_history_channel", int),
    ("queue_general_channel", int),
    ("admin_roles", list),
    ("admin_roles.list_items", int),
    ("helper_staff_roles", list),
    ("helper_staff_roles.list_items", int),
    ("members_for_channels", list),
    ("members_for_channels.list_items", (int, bool)),
    ("roles_for_channels", list),
    ("roles_for_channels.list_items", int),
    ("PLACEMENT_PLAYER_MMR", int),
    ("placement_role_id", int),
    ("frequently_tagged_role_id", int),
    ("restricted_role_id", int),
    ("muted_role_id", int),
    ("queue_messages", bool),
    ("sec_between_queue_msgs", int),
    ("username", str),
    ("password", str),
    ("url", str),
    ("track_type", str),
    ("FIRST_EVENT_TIME", int),
    ("FIRST_EVENT_TIME_DESCRIPTION", str),
    ("JOINING_TIME", int),
    ("QUEUE_OPEN_TIME", int),
    ("DISPLAY_OFFSET_MINUTES", int),
    ("EXTENSION_TIME", int),
    ("ROOM_JOIN_PENALTY_TIME", int),
    ("MOGI_LIFETIME", int),
    ("SUB_RANGE_MMR_ALLOWANCE", int),
    ("SUB_MESSAGE_LIFETIME_SECONDS", int),
    ("ROOM_MMR_THRESHOLD", int),
    ("MATCHMAKING_BOTTOM_MMR", (int, type(None))),
    ("MATCHMAKING_TOP_MMR", (int, type(None))),
    ("USE_THREADS", bool)
]

def check(config: dict):
    for required_field_name, required_field_type in STRONGLY_REQUIRED_FIELDS:
        check_list_items = required_field_name.endswith(".list_items")
        if check_list_items:
            required_field_name = required_field_name[:-len(".list_items")]

        if required_field_name not in config:
            raise ValueError(f"Did not find field '{required_field_name}' in config. Your config appears to be out of date.")
        field_data = config[required_field_name]
        if type(required_field_type) is not tuple:
            required_field_type = (required_field_type,)
        if check_list_items:
            for item in field_data:
                if type(item) not in required_field_type:
                    raise TypeError(
                        f"""For field '{required_field_name}', expected items to be of type {" or ".join([str(t) for t in required_field_type])}, but items in config were of type {type(item)}""")
        else:

            if type(field_data) not in required_field_type:
                raise TypeError(
                    f"""For field '{required_field_name}', expected type {" or ".join([str(t) for t in required_field_type])}, but config type is {type(field_data)}""")

    # Validate tier channels data if needed
    if not config["USE_THREADS"]:
        required_field_name = "TIER_CHANNELS"
        if required_field_name not in config:
            raise ValueError(f"Did not find field '{required_field_name}' in config. Your config appears to be out of date.")
        tier_channel_data = config[required_field_name]
        if type(tier_channel_data) is not dict:
            raise TypeError(f"""For field '{required_field_name}', expected type dict, but config type is {type(tier_channel_data)}""")
        for k, v in tier_channel_data.items():
            if type(k) is not str:
                raise TypeError(f"""For field '{required_field_name}', the keys must all be of type str, but a key of type {type(k)} was found.""")
            needed_keys = [("tier_role_id", int), ("channel_ids", list), ("role_ids_can_see_already", list)]
            for sub_k, sub_type in needed_keys:
                if sub_k not in v:
                    raise ValueError(f"Did not find field '{sub_k}' in each item of TIER_CHANNELS in the config. Your config appears to be out of date.")
                sub_data = v[sub_k]
                if type(sub_data) is not sub_type:
                    raise TypeError(f"""For field '{sub_k}' in TIER_CHANNELS data, found type {type(sub_data)}, expected {sub_type}.""")


    print("Config file validated.")

