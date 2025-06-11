import json
from enum import Enum, auto
from typing import TypeVar, Type, List
import config_checker

_T = TypeVar('_T')

with open('./config.json', 'r') as cjson:
    CONFIG = json.load(cjson)


class Server(Enum):
    MKW = auto()
    MK8DX = auto()
    MKWorld = auto()


if CONFIG["lounge"] == "MKW":
    SERVER = Server.MKW
elif CONFIG["lounge"] == "MK8DX":
    SERVER = Server.MK8DX
elif CONFIG["lounge"] == "MKWorld":
    SERVER = Server.MKWorld
else:
    raise ValueError(
        f"{CONFIG['lounge']} is not a valid option for the 'lounge' attribute in the config.")


def divide_chunks(list_: list, chunk_size: int):
    # looping till length l
    for i in range(0, len(list_), chunk_size):
        yield list_[i:i + chunk_size]


def flatten(matrix: List[List[_T]]) -> List[_T]:
    flat_list = []
    for row in matrix:
        flat_list.extend(row)
    return flat_list


def is_int(var: str) -> bool:
    try:
        int(var)
        return True
    except ValueError:
        return False


config_checker.check(CONFIG)
