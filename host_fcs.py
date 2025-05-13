import common
from typing import Dict
from collections.abc import Iterable
import aiohttp

MKW_HOST_API_URL = f"https://mkwlounge.gg/api/hostfc.php?discord_guild_id={common.CONFIG['guild_id']}&discord_user_id="

SESSION = None


def create_new_session():
    global SESSION
    SESSION = aiohttp.ClientSession()


async def _get_mk8dx_hosts(discord_ids: Iterable[str]) -> Dict[str, str]:
    return {}


async def _get_mkworld_hosts(discord_ids: Iterable[str]) -> Dict[str, str]:
    return {}


async def _get_mkw_hosts(discord_ids: Iterable[str]) -> Dict[str, str]:
    request_url = MKW_HOST_API_URL + ",".join(discord_ids)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get(request_url) as r:
            if r.status != 200:
                print(f"hostfc endpoint returned status {r.status}")
                return {}
            results = await r.json()
            if "status" not in results or results["status"] != "success":
                print(
                    f"hostfc endpoint returned unsuccessful results {results}")
                return {}
            host_mapping = {}
            for item in results["results"]:
                host_mapping[item["discord_user_id"]] = item["fc"]
            return host_mapping


async def get_hosts(discord_ids: Iterable[str]) -> Dict[str, str]:
    if common.SERVER is common.Server.MKW:
        return await _get_mkw_hosts(discord_ids)
    elif common.SERVER is common.Server.MK8DX:
        return await _get_mk8dx_hosts(discord_ids)
    elif common.SERVER is common.Server.MKWorld:
        return await _get_mkworld_hosts(discord_ids)
    else:
        raise ValueError("Bad config settings for server.")
