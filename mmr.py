import aiohttp
from mogi_objects import Player
import common
import discord
from typing import List
import asyncio

headers = {'Content-type': 'application/json'}

class RatingsNotReady(Exception):
    pass

class Ratings:
    def __init__(self):
        self.first_run_complete = False
        self.ratings = {}

    async def update_ratings(self):
        if common.SERVER is common.Server.MK8DX:
            rating_func = self._pull_mk8dx_ratings
        elif common.SERVER is common.Server.MKW:
            rating_func = self._pull_mkw_ratings
        else:
            raise Exception("Unreachable code.")

        status = await rating_func()
        # If we failed to pull ratings, wait 60 seconds and try again
        if not status:
            print(f"Failed to pull ratings for {common.SERVER.name}. Waiting 60 seconds and trying again.")
            await asyncio.sleep(60)
            status = await rating_func()
            # If we failed to pull ratings again, fail.
            if not status:
                print(f"Failed to pull ratings for {common.SERVER.name} on 2nd attempt. Skipping.")
                return
        self.first_run_complete = True

    async def _pull_mk8dx_ratings(self) -> bool:
        pass

    async def _pull_mkw_ratings(self):
        base_url = f"{common.CONFIG["url"]}/api/ladderplayer.php?ladder_type={common.CONFIG["track_type"]}all&fields=discord_user_id,current_mmr"
        # Do stuff
        resp = {}
        self._parse_mkw_ratings(resp)

    def _parse_mk8dx_ratings(self, results: dict):
        pass

    def _parse_mkw_ratings(self, results: dict):
        pass

    def get_rating_from_discord_id(self, discord_id: str) -> int | None:
        if not self.first_run_complete:
            raise RatingsNotReady("Ratings not pulled yet.")
        return self.ratings.get(discord_id)

    def get_rating(self, members: List[discord.User | discord.Member]) -> List[Player]:
        if not self.first_run_complete:
            raise RatingsNotReady("Ratings not pulled yet.")
        return []


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
