import aiohttp
import discord
from mogi_objects import Player

headers = {'Content-type': 'application/json'}


async def mk8dx_150cc_mmr(url, members):
    base_url = url + '/api/ladderplayer.php?ladder_type=rt&'
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


async def get_mmr_from_discord_id(discord_id):
    base_url = "https://www.mkwlounge.gg" + '/api/ladderplayer.php?ladder_type=rt&'
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


async def get_mmr(config, members):
    return await mk8dx_150cc_mmr(config, members)


async def mk8dx_150cc_fc(config, name):
    base_url = config["url"] + '/api/player?'
    request_url = base_url + f'name={name}'
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(request_url, headers=headers) as resp:
            if resp.status != 200:
                return None
            player_data = await resp.json()
            if 'switchFc' not in player_data.keys():
                return None
            return player_data['switchFc']
