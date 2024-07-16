import aiohttp
import discord
from mogi_objects import Player
import common

headers = {'Content-type': 'application/json'}


# class LoungeData:
#     def __init__(self):
#         self._data = None

#     async def lounge_api_full(self):
#         async with aiohttp.ClientSession() as session:
#             async with session.get("https://www.mk8dx-lounge.com/api/player/list") as response:
#                 if response.status == 200:
#                     _data_full = await response.json()
#                     self._data = [
#                         player for player in _data_full['players'] if "discordId" in player]

#     def data(self):
#         return self._data


# lounge_data = LoungeData()


async def mk8dx_mmr(url, members):
    base_url = url + '/api/player?'
    players = []
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            auth=aiohttp.BasicAuth(
                "username", "password")) as session:
            for member in members:
                request_text = f"discordId={member.id}"
                request_url = base_url + request_text
                async with session.get(request_url, headers=headers) as resp:
                    if resp.status != 200:
                        players.append(None)
                        continue
                    player_data = await resp.json()
                    if 'mmr' not in player_data.keys():
                        players.append(
                            Player(member, player_data['name'], None))
                        continue
                    players.append(
                        Player(member, player_data['name'], player_data['mmr']))
    except:
        print(f"Fetch for player {members[0]} has failed.", flush=True)
    return players


async def mkw_mmr(url, members, track_type):
    tracks = track_type
    base_url = url + '/api/ladderplayer.php?ladder_type=' + tracks + '&'
    players = []
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            auth=aiohttp.BasicAuth(
                "username", "password")) as session:
            for member in members:
                request_text = f"discord_user_id={member.id}"
                request_url = base_url + request_text
                async with session.get(request_url, headers=headers) as resp:
                    if resp.status != 200:
                        players.append(None)
                        continue
                    result = await resp.json()
                    player_data = result["results"][0]
                    if 'current_mmr' not in player_data.keys():
                        players.append(
                            Player(member, player_data['player_name'], None))
                        continue
                    players.append(
                        Player(member, player_data['player_name'], player_data['current_mmr']))
    except:
        print(f"Fetch for player {members[0]} has failed.", flush=True)
    return players


async def get_mmr(url, members, track_type):
    if common.SERVER is common.Server.MK8DX:
        return await mk8dx_mmr(url, members)
    elif common.SERVER is common.Server.MKW:
        return await mkw_mmr(url, members, track_type)


async def mk8dx_get_mmr_from_discord_id(url, discord_id):
    base_url = url + '/api/player?'
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(
        timeout=timeout,
        auth=aiohttp.BasicAuth(
            "username", "password")) as session:
        request_text = f"discordId={discord_id}"
        request_url = base_url + request_text
        async with session.get(request_url, headers=headers) as resp:
            if resp.status != 200:
                return "Player does not exist"
            player_data = await resp.json()
            if 'mmr' not in player_data.keys():
                return "Player has no mmr"
            return player_data['mmr']


async def mkw_get_mmr_from_discord_id(url, discord_id, track_type):
    tracks = track_type
    base_url = url + \
        '/api/ladderplayer.php?ladder_type=' + tracks + '&'
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(
        timeout=timeout,
        auth=aiohttp.BasicAuth(
            "username", "password")) as session:
        request_text = f"discord_user_id={discord_id}"
        request_url = base_url + request_text
        async with session.get(request_url, headers=headers) as resp:
            if resp.status != 200:
                return "Player does not exist"
            result = await resp.json()
            player_data = result["results"][0]
            if 'current_mmr' not in player_data.keys():
                return "Player has no mmr"
            return player_data['current_mmr']


async def get_mmr_from_discord_id(url, discord_id, track_type):
    if common.SERVER is common.Server.MK8DX:
        return await mk8dx_get_mmr_from_discord_id(url, discord_id)
    elif common.SERVER is common.Server.MKW:
        return await mkw_get_mmr_from_discord_id(url, discord_id, track_type)
