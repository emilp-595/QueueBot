import aiohttp
from mogi_objects import Player
import common
import discord
from typing import List
import asyncio
import traceback

headers = {'Content-type': 'application/json'}


class RatingsNotReady(Exception):
    pass


class RatingRequestFailure(Exception):
    pass


class BadRatingData(RatingRequestFailure):
    pass


class BadPlayerDataLength(BadRatingData):
    pass


class BadPlayerData(BadRatingData):
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
        elif common.SERVER is common.Server.MKWorld:
            rating_func = self._pull_mkworld_ratings
        else:
            raise Exception("Unreachable code.")

        status = await rating_func()
        # If we failed to pull ratings, wait 60 seconds and try again
        if not status:
            print(
                f"Failed to pull ratings for {common.SERVER.name}. Waiting 60 seconds and trying again.")
            await asyncio.sleep(60)
            status = await rating_func()
            # If we failed to pull ratings again, fail.
            if not status:
                print(
                    f"Failed to pull ratings for {common.SERVER.name} on 2nd attempt. Skipping.")
                return
        self.first_run_complete = True

    async def _pull_mk8dx_ratings(self) -> bool:
        url = f"""{common.CONFIG["url"]}/api/player/list"""
        return await Ratings._pull_ratings(url, self._parse_mk8dx_ratings, self._validate_mk8dx_response)

    async def _pull_mkw_ratings(self) -> bool:
        url = f"""{common.CONFIG["url"]}/api/ladderplayer.php?ladder_type={common.CONFIG["track_type"]}&all&fields=discord_user_id,current_mmr,player_name"""
        return await Ratings._pull_ratings(url, self._parse_mkw_ratings, self._validate_mkw_response)

    async def _pull_mkworld_ratings(self) -> bool:
        url = f"""{common.CONFIG["url"]}/api/player/list"""
        return await Ratings._pull_ratings(url, self._parse_mk8dx_ratings, self._validate_mk8dx_response)

    @staticmethod
    async def _pull_ratings(url, parser, validator) -> bool:
        """Returns True if the ratings were pulled and successfully stored."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    print(f"{common.SERVER.name} returned status {response.status}")
                    return False
                data = await response.json()
                try:
                    validator(data)
                except RatingRequestFailure:
                    print(
                        f"{common.SERVER.name}'s data from API was formatted incorrectly.")
                    print(traceback.format_exc())
                    return False
                parser(data)
                return True
        return False  # Didn't run request successfully, so we return it was a failure

    def _validate_mk8dx_response(self, results: dict):
        if not isinstance(results, dict):
            raise BadRatingData("Response is not a dictionary")
        all_players = results.get("players")
        if all_players is None:
            raise BadRatingData(
                "Key word 'players' not found in JSON response.")
        #
        required_player_amount = 10000
        if len(all_players) < required_player_amount:
            raise BadPlayerDataLength(
                f"Not enough players found in the JSON response. Required {required_player_amount} players in JSON response, only found {len(all_players)} players in JSON response."
                "")

        strongly_required_fields = [("name", str)]
        weakly_required_fields = [("discordId", str), ("mmr", int)]
        for player in all_players:
            # Ensure all strongly required fields are in the player JSON and
            # that the type is correct
            for strongly_req_field_name, strongly_req_field_type in strongly_required_fields:
                if strongly_req_field_name not in player:
                    raise BadPlayerData(
                        f"Missing required field '{strongly_req_field_name}' in the following player: {player}")
                field_data = player[strongly_req_field_name]
                if not isinstance(field_data, strongly_req_field_type):
                    raise BadPlayerData(
                        f"For field '{strongly_req_field_name}', expected type '{strongly_req_field_type}' received {type(field_data)} for player: {player}")
            # Ensure that if the weakly required field is in the JSON, the type
            # is correct
            for weakly_req_field_name, weakly_req_field_type in weakly_required_fields:
                if weakly_req_field_name in player:
                    field_data = player[weakly_req_field_name]
                    if not isinstance(field_data, weakly_req_field_type):
                        raise BadPlayerData(
                            f"For field '{weakly_req_field_name}', expected type '{weakly_req_field_type}' received {type(field_data)} for player: {player}")

    def _parse_mk8dx_ratings(self, results: dict):
        self.ratings.clear()
        all_players = results.get("players")
        for player in all_players:
            discord_id = player.get("discordId")
            if discord_id is None:
                continue
            rating = common.CONFIG["PLACEMENT_PLAYER_MMR"] if player.get(
                "mmr") is None else player.get("mmr")
            self.ratings[discord_id] = (rating, player["name"])

    def _validate_mkw_response(self, results: dict):
        if not isinstance(results, dict):
            raise BadRatingData("Response is not a dictionary")
        mkw_status = results.get("status")
        if mkw_status is None:
            raise BadRatingData(
                "Key word 'status' not found in JSON response.")
        if mkw_status != "success":
            raise BadRatingData(
                f"'status' had value of {mkw_status} in JSON response.")

        all_players = results.get("results")
        if all_players is None:
            raise BadRatingData(
                "Key word 'results' not found in JSON response.")
        #
        required_player_amount = 1000
        if len(all_players) < required_player_amount:
            raise BadPlayerDataLength(
                f"Not enough players found in the JSON response. Required {required_player_amount} players in JSON response, only found {len(all_players)} players in JSON response."
                "")

        for player in all_players:
            if "player_name" not in player:
                raise BadPlayerData(
                    f"Missing required field 'player_name' in the following player: {player}")
            if "discord_user_id" not in player:
                raise BadPlayerData(
                    f"Missing required field 'discord_user_id' in the following player: {player}")
            if "current_mmr" not in player:
                raise BadPlayerData(
                    f"Missing required field 'current_mmr' in the following player: {player}")
            player_name = player.get("player_name")
            discord_user_id = player.get("discord_user_id")
            current_mmr = player.get("current_mmr")
            if not (
                isinstance(
                    discord_user_id,
                    str) or discord_user_id is None):
                raise BadPlayerData(
                    f"For field 'discord_user_id', expected type 'str' or None, received {type(discord_user_id)} for player: {player}")
            if not isinstance(current_mmr, int):
                raise BadPlayerData(
                    f"For field 'current_mmr', expected type 'int' received {type(current_mmr)} for player: {player}")
            if not isinstance(player_name, str):
                raise BadPlayerData(
                    f"For field 'player_name', expected type 'str' received {type(player_name)} for player: {player}")

    def _parse_mkw_ratings(self, results: dict):
        self.ratings.clear()
        all_players = results.get("results")
        for player in all_players:
            discord_user_id = player.get("discord_user_id")
            if discord_user_id is not None:
                self.ratings[player["discord_user_id"]] = (
                    player["current_mmr"], player["player_name"])

    def get_rating_from_discord_id(self, discord_id: int | str) -> int | None:
        discord_id = str(discord_id)
        if not self.first_run_complete:
            raise RatingsNotReady("Ratings not pulled yet.")
        if discord_id in self.ratings:
            return self.ratings.get(discord_id)[0]
        # Make clear that we intend to return None by explicitly doing so
        else:
            return None

    def get_rating(
            self, members: List[discord.User | discord.Member]) -> List[Player]:
        if not self.first_run_complete:
            raise RatingsNotReady("Ratings not pulled yet.")
        all_players = []
        for member in members:
            # Are discord member IDs already strings...?
            member_id = str(member.id)
            if member_id not in self.ratings:
                continue
            rating, name = self.ratings[member_id]
            all_players.append(Player(member, name, rating))
        return all_players
