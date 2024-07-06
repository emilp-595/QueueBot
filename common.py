import json
from typing import TypeVar, Type, List

_T = TypeVar('T')

with open('./config.json', 'r') as cjson:
    CONFIG = json.load(cjson)


def divide_chunks(list_: list, chunk_size: int):
    # looping till length l
    for i in range(0, len(list_), chunk_size):
        yield list_[i:i + chunk_size]


def flatten(matrix: List[List[_T]]) -> List[_T]:
    flat_list = []
    for row in matrix:
        flat_list.extend(row)
    return flat_list
