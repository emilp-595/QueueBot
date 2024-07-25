from __future__ import annotations

import traceback
import common
from contextlib import suppress
from common import flatten
import random
import discord
from datetime import datetime, timezone, timedelta
from discord.ui import View
from typing import List, Tuple, Callable, Set
import host_fcs


class PrepFailure(Exception):
    pass


class WrongChannelType(TypeError, PrepFailure):
    pass


class RoleFailure(PrepFailure):
    pass


class RoleAddFailure(RoleFailure):
    pass


class RoleNotFound(RoleFailure):
    pass


class NoFreeChannels(PrepFailure):
    pass


def average(list_: List[int | float]) -> float:
    return sum(list_) / len(list_)


class Mogi:
    ALGORITHM_STATUS_INSUFFICIENT_PLAYERS = 1
    ALGORITHM_STATUS_2_OR_MORE_ROOMS = 2
    ALGORITHM_STATUS_SUCCESS_FOUND = 3
    ALGORITHM_STATUS_SUCCESS_EMPTY = 4
    ALGORITHM_STATUS_SUCCESS_SOME_INVALID = 5

    def __init__(self, sq_id: int, max_players_per_team: int, players_per_room: int, mogi_channel: discord.TextChannel,
                 is_automated=False, start_time=None, display_time=None, additional_extension_minutes=0):
        self.started = False
        self.gathering = False
        self.making_rooms_run = False
        self.making_rooms_run_time = None
        self.sq_id = sq_id
        self.max_player_per_team = max_players_per_team
        self.players_per_room = players_per_room
        self.mogi_channel = mogi_channel
        self.teams: List[Team] = []
        self.rooms: List[Room] = []
        self.is_automated = is_automated
        self.start_time = start_time if is_automated else None
        self.display_time = display_time if is_automated else None
        self.additional_extension = timedelta(
            minutes=additional_extension_minutes)

    @property
    def num_players(self):
        """Returns the total number of players in teams where all players have confirmed"""
        return sum(len(t.players) for t in self.teams if t.all_registered())

    @property
    def num_teams(self):
        """Returns the total number of teams where all players have confirmed"""
        return self.count_registered()

    @property
    def max_possible_rooms(self) -> int:
        """Returns the maximum possible number of rooms based on teams where all players have confirmed. Depending on range cutoffs or
        the specifics of the algorithm used to make the actual rooms, this number could be higher than the final number of rooms."""
        return self.num_players // self.players_per_room

    @staticmethod
    def _minimize_range(players: List[Player], num_players: int) -> List[Player] | None:
        """Returns a collection of players (the number of players in the collection is the given num_players parameter) whose has the smallest
        mmr spread. If the number of players in the given list is smaller than the request num_players collection size, or the num_players is less than 2 (doesn't make sense), None is returned."""
        # The number of players we were given is less than the collection size we are supposed to return, so return None
        if len(players) < num_players:
            return None
        if num_players <= 1:
            return None
        # Sort the players so we easily know the player with the lowest rating and highest rating in any given collection
        sorted_players = sorted(players)
        # In the beginning, the best found collection of players is the first 12
        best_collection = sorted_players[0:num_players]
        cur_min = best_collection[-1].adjusted_mmr - best_collection[0].adjusted_mmr
        # Find the collection of players with the least rating spread
        for lowest_player_index, highest_player in enumerate(sorted_players[num_players:], 1):
            lowest_player = sorted_players[lowest_player_index]
            cur_range = highest_player.adjusted_mmr - lowest_player.adjusted_mmr
            if cur_range < cur_min:
                cur_min = cur_range
                best_collection = sorted_players[lowest_player_index:
                                                 lowest_player_index + num_players]
        return best_collection

    def _all_room_final_list_algorithm(self, valid_players_check: Callable[[List[Player]], bool]) -> Tuple[List[List[Player]], int]:
        if self.max_possible_rooms == 0:
            return [], Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS
        all_confirmed_players = self.players_on_confirmed_teams()
        first_late_player_index = (self.num_players // self.players_per_room) * self.players_per_room
        on_time_players = sorted(all_confirmed_players[:first_late_player_index], reverse=True)
        late_players = all_confirmed_players[first_late_player_index:]
        player_rooms: List[List[Player]] = list(common.divide_chunks(on_time_players, self.players_per_room))

        any_invalid = False
        for pr_index, player_room in enumerate(player_rooms, 0):
            for lp_index in range(len(late_players)+1):
                current_late_check_players = late_players[0:lp_index]
                best_collection = Mogi._minimize_range(player_room + current_late_check_players, self.players_per_room)
                if valid_players_check(best_collection):
                    best_collection.sort(reverse=True)
                    # Get all the players who were swapped out of the room and put them at the front of the late player list
                    swapped_out_players = [p for p in player_room if p not in best_collection]
                    swapped_in_players = [p for p in best_collection if p not in player_room]
                    late_players = swapped_out_players + list(filter(lambda p: p not in swapped_in_players, late_players))
                    player_rooms[pr_index] = best_collection
                    break
            else:
                # After checking all late players, no room player list with a valid range could be found for this room
                any_invalid = True

        return player_rooms, (Mogi.ALGORITHM_STATUS_SUCCESS_SOME_INVALID if any_invalid else Mogi.ALGORITHM_STATUS_SUCCESS_FOUND)

    def _one_room_final_list_algorithm(self, valid_players_check: Callable[[List[Player]], bool]) -> Tuple[List[Player], int]:
        if self.max_possible_rooms == 0:
            return [], Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS
        if self.max_possible_rooms > 1:
            confirmed_players = self.players_on_confirmed_teams()
            return sorted(confirmed_players[0:self.players_per_room * self.max_possible_rooms], reverse=True), Mogi.ALGORITHM_STATUS_2_OR_MORE_ROOMS
        # At this point, we can only make one possible room, so our algorithm will be used
        confirmed_players = self.players_on_confirmed_teams()
        cur_check_list = list(confirmed_players[0:self.players_per_room])
        late_players = list(confirmed_players[self.players_per_room:])

        while True:
            best_collection = Mogi._minimize_range(
                cur_check_list, self.players_per_room)
            if valid_players_check(best_collection):
                return sorted(best_collection, reverse=True), Mogi.ALGORITHM_STATUS_SUCCESS_FOUND
            if len(late_players) == 0:
                break
            cur_check_list.append(late_players.pop(0))
        # Even after checking the late players, we did not find
        return best_collection, Mogi.ALGORITHM_STATUS_SUCCESS_EMPTY

    def _mk8dx_generate_final_list(self) -> List[Player]:
        confirmed_players = self.players_on_confirmed_teams()
        if self.max_possible_rooms == 0:
            return confirmed_players
        return sorted(confirmed_players[0:self.players_per_room * self.max_possible_rooms], reverse=True)

    def _mkw_generate_final_list(self, valid_players_check: Callable[[List[Player]], bool]) -> List[Player]:
        result, status = self._all_room_final_list_algorithm(valid_players_check)
        if status is Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS:
            return self.players_on_confirmed_teams()
        return sorted(common.flatten(result), reverse=True)  # I do not want to formally prove that sorting here is correct

    def generate_proposed_list(self, valid_players_check: Callable[[List[Player]], bool] = None) -> List[Player]:
        """Algorithm that generates a proposed list of players that will play. This algorithm may differ between
        MK8DX and MKW. The algorithm is allowed to propose any list of players it wants to. Among several possibilities,
        this allows the algorithm to change the order of the players in the returned list, add or remove players,
        and more.

        The algorithm may or may not enforce a hard check of the valid players. That is up to the implemented
        algorithm."""
        if common.SERVER is common.Server.MK8DX:
            return self._mk8dx_generate_final_list()
        elif common.SERVER is common.Server.MKW:
            return self._mkw_generate_final_list(valid_players_check)
        else:
            raise ValueError(f"Unknown server in config: {common.Server}")

    def _count_cancelled(self, player_list: List[Player], valid_players_check: Callable[[List[Player]], bool] = None) -> int:
        """Using the provided valid players check function and player list, returns the number of rooms that would be cancelled."""
        if valid_players_check is None:
            return 0
        return sum(1 for room_players in common.divide_chunks(player_list, self.players_per_room) if not valid_players_check(room_players))

    def _any_cancelled(self, player_list: List[Player], valid_players_check: Callable[[List[Player]], bool] = None) -> bool:
        return self._count_cancelled(player_list, valid_players_check) > 0

    def any_room_cancelled(self, valid_players_check: Callable[[List[Player]], bool] = None) -> bool:
        """Using the provided valid players check function, returns if any of the rooms will be cancelled."""
        if valid_players_check is None:
            return False
        proposed_list = self.generate_proposed_list(valid_players_check)
        return self._any_cancelled(proposed_list, valid_players_check)

    def check_player(self, member):
        for team in self.teams:
            if team.has_player(member):
                return team
        return None

    def count_registered(self) -> int:
        """Returns the number of teams that are registered"""
        return sum(1 for team in self.teams if team.all_registered())

    def confirmed_teams(self) -> List["Team"]:
        return [team for team in self.teams if team.all_registered()]

    def players_on_confirmed_teams(self) -> List[Player]:
        return flatten([team.players for team in self.confirmed_teams()])

    def all_room_channel_ids(self) -> Set[int]:
        return {r.channel.id for r in self.rooms if r is not None and r.channel is not None}

    def channel_id_in_rooms(self, channel_id: int):
        return channel_id in self.all_room_channel_ids()

    def get_room_from_channel_id(self, channel_id: int):
        for room in self.rooms:
            if room is None or room.channel is None:
                continue
            if room.channel.id == channel_id:
                return room
        return None

    async def populate_host_fcs(self):
        all_hosts = {str(plr.member.id): plr for plr in filter(
            lambda p: p.host, self.players_on_confirmed_teams())}
        hosts = await host_fcs.get_hosts(all_hosts)
        for host_discord_id, host_fc in hosts.items():
            player: Player = all_hosts.get(host_discord_id)
            if player is not None:
                player.host_fc = host_fc

    async def assign_roles(self, guild: discord.Guild):
        for room in self.rooms:
            try:
                to_skip = None if room.room_role is None else {room.room_role.id}
                await room.assign_roles(guild=guild, role_skip=to_skip)
            except RoleAddFailure:
                pass



class Room:
    def __init__(self, teams, room_num: int, channel: discord.Thread | discord.TextChannel, tier_info):
        self.teams: List["Team"] = teams
        self.room_num = room_num
        self.channel = channel
        self.view = None
        self.finished = False
        self.host_list: List["Player"] = []
        self.subs: List[int] = []
        self.tier_info = tier_info
        self.room_role = None

    @property
    def tier(self) -> str:
        if common.SERVER is common.Server.MK8DX:
            return get_tier_mk8dx(round(self.avg_mmr) - 500)
        if common.SERVER is common.Server.MKW:
            return str(get_tier_mkw(self.avg_mmr, self.tier_info))

    @property
    def tier_collection(self) -> str:
        if common.SERVER is common.Server.MK8DX:
            return self.tier
        if common.SERVER is common.Server.MKW:
            tier_number = self.tier
            if not common.is_int(tier_number):
                return "HT"
            tier_number = int(tier_number)
            if tier_number <= 2:
                return "LT"
            if tier_number >= 5:
                return "HT"
            return "MT"

    @property
    def mmr_high(self) -> int:
        if self.teams is None:
            return None
        return max(self.players).adjusted_mmr

    @property
    def mmr_low(self) -> int:
        if self.teams is None:
            return None
        return min(self.players).adjusted_mmr

    @property
    def avg_mmr(self) -> int:
        if self.teams is None:
            return 0
        return int(average([p.mmr for p in self.players]))

    @property
    def players(self) -> List[Player]:
        if self.teams is None:
            return []
        return flatten([t.players for t in self.teams])

    def get_player_list(self):
        return [player.member.id for team in self.teams for player in team.players]

    def create_host_list(self):
        all_hosts = list(filter(lambda p: p.host, self.players))
        random.shuffle(all_hosts)
        self.host_list.clear()
        self.host_list.extend(all_hosts)

    def get_host_str(self) -> str:
        if len(self.host_list) == 0:
            return ""
        host_strs = []
        for i, player in enumerate(self.host_list, 1):
            host_strs.append(f"{i}. {player.member.display_name}")
            # First player on the list should be bold
            if i == 1:
                host_strs[0] = f"**{host_strs[0]}**"
        result = f"Host: {', '.join(host_strs)}"
        if common.SERVER is common.Server.MKW and self.host_list[0].host_fc is not None:
            result += f"\n**Host ({self.host_list[0].member.display_name}) Friend Code: {self.host_list[0].host_fc}**"
        return result

    def check_player(self, member):
        if self.teams is None:
            return None
        for team in self.teams:
            if team.has_player(member):
                return team
        return None

    async def assign_member_room_role(self, member: discord.Member):
        if self.room_role is None:
            raise RoleNotFound(f"Could not find role for tier {self.tier}")
        try:
            await member.add_roles(self.room_role)
        except:
            raise RoleAddFailure(f"Failed to add role {self.room_role.name} to {member}\n")

    async def assign_roles(self, guild: discord.Guild, role_skip=None):
        role_add_fail_text = ""
        role_skip = set() if role_skip is None else set(role_skip)
        for player in self.players:
            updated_member = guild.get_member(player.member.id)
            if updated_member is not None:
                member_role_ids = {r.id for r in updated_member.roles}
                if len(role_skip.intersection(member_role_ids)) > 0:
                    continue
            try:
                await self.assign_member_room_role(player.member)
            except RoleAddFailure:
                role_add_fail_text += f"Failed to add role {self.room_role.name} to {player.member}\n"
                print(traceback.format_exc())

        if role_add_fail_text != "":
            await self.channel.send(role_add_fail_text)
            raise RoleAddFailure(role_add_fail_text)

    async def prepare_room_channel(self, guild: discord.Guild, all_events: List[Mogi | None]):
        if common.CONFIG["USE_THREADS"]:
            return
        
        # Find the available tier channels for the tier (collection)
        tier_collection = self.tier_collection
        tier_data = common.CONFIG["TIER_CHANNELS"][tier_collection]
        free_tier_channel_ids = list(tier_data["channel_ids"])
        for event in all_events:
            if event is None:
                continue
            for channel_id in event.all_room_channel_ids():
                with suppress(ValueError):
                    free_tier_channel_ids.remove(channel_id)
        # If there are no available tier channels, raise an exception
        if len(free_tier_channel_ids) == 0:
            raise NoFreeChannels(f"No free channels for tier {tier_collection}")
        
        # Get the discord.GuildChannel of the first available channel id
        found_channel = guild.get_channel(free_tier_channel_ids[0])
        if not isinstance(found_channel, discord.TextChannel):
            raise WrongChannelType(f"For tier {tier_collection}, channel id {channel_id} is of type {type(found_channel)}, expected discord.TextChannel")
        self.channel = found_channel

        # Assign the role for the tier collection to all players in the room so they can see the tier
        tier_role_id = tier_data["tier_role_id"]
        self.room_role = guild.get_role(tier_role_id)
        if self.room_role is None:
            raise RoleNotFound(f"Could not find role for role id {tier_role_id} for tier {tier_collection}")
        await self.assign_roles(guild=guild, role_skip=tier_data["role_ids_can_see_already"]+[tier_role_id])


class Team:
    def __init__(self, players: List["Player"]):
        self.players = players

    def get_mentions(self):
        """Return a string where all players on the team are discord @'d"""
        return " ".join([p.member.mention for p in self.players])

    @property
    def avg_mmr(self):
        return average([p.mmr for p in self.players])

    def all_registered(self):
        """Returns if all players on the team are registered"""
        return all(player.confirmed for player in self.players)

    def has_player(self, member):
        return any(player.member.id == member.id for player in self.players)

    def get_player(self, member: discord.Member) -> Player | None:
        for player in self.players:
            if player.member.id == member.id:
                return player
        return None

    def num_confirmed(self):
        """Returns the number of confirmed players in the team"""
        return sum(1 for player in self.players if player.confirmed)

    def get_unconfirmed(self):
        """Returns a list of players on the team who have not confirmed yet."""
        return [player for player in self.players if not player.confirmed]

    def __lt__(self, other):
        return self.avg_mmr < other.avg_mmr

    def __gt__(self, other):
        return other < self

    # def __eq__(self, other):
    #     if self.avg_mmr == other.avg_mmr:
    #         return True
    #     return False

    def __str__(self):
        return ", ".join([p.lounge_name for p in self.players])


class Player:
    def __init__(self, member: discord.Member, lounge_name: str, mmr: int, confirmed=False, host=False):
        self.member = member
        self.lounge_name = lounge_name
        self.mmr = mmr
        self.confirmed = confirmed
        self.score = 0
        self.host = host
        self.host_fc = None

    @property
    def adjusted_mmr(self):
        minimum_mmr = common.CONFIG["MATCHMAKING_BOTTOM_MMR"]
        maximum_mmr = common.CONFIG["MATCHMAKING_TOP_MMR"]
        if minimum_mmr is None or maximum_mmr is None:
            return self.mmr
        if self.mmr < minimum_mmr:
            return minimum_mmr
        if self.mmr > maximum_mmr:
            return maximum_mmr
        return self.mmr

    @property
    def is_matchmaking_mmr_adjusted(self):
        return self.mmr != self.adjusted_mmr

    @property
    def mention(self):
        """String that, when sent in a Discord message, will mention and ping the player."""
        if self.member is None:
            return "<@!1>"
        return self.member.mention

    def __repr__(self):
        return f"{__class__.__name__}(member={self.member}, lounge_name={self.lounge_name}, mmr={self.mmr}, confirmed={self.confirmed})"

    def __lt__(self, other: Player):
        return self.mmr < other.mmr


class VoteView(View):
    def __init__(self, players, thread, mogi: Mogi, room: Room, penalty_time: int):
        super().__init__()
        self.players = players
        self.room_channel: discord.Thread | discord.TextChannel = thread
        self.mogi = mogi
        self.room = room
        self.header_text = ""
        self.teams_text = ""
        self.found_winner = False
        self.penalty_time = penalty_time
        self.votes = {"FFA": [],
                      "2v2": [],
                      "3v3": [],
                      "4v4": [],
                      "6v6": []
                      }

        self.add_button("FFA", self.general_vote_callback)
        self.add_button("2v2", self.general_vote_callback)
        self.add_button("3v3", self.general_vote_callback)
        self.add_button("4v4", self.general_vote_callback)

        if common.SERVER is common.Server.MKW:
            self.add_button("6v6", self.general_vote_callback)

    async def make_teams(self, format_):
        if common.SERVER is common.Server.MKW:
            self.mogi.making_rooms_run_time = datetime.now(timezone.utc)
        elif common.SERVER is common.Server.MK8DX:
            self.mogi.making_rooms_run_time = self.mogi.start_time + \
                timedelta(minutes=5)
        random.shuffle(self.players)

        room = self.mogi.get_room_from_channel_id(self.room_channel.id)

        msg = f"""**Poll Ended!**

1) FFA - {len(self.votes['FFA'])}
2) 2v2 - {len(self.votes['2v2'])}
3) 3v3 - {len(self.votes['3v3'])}
4) 4v4 - {len(self.votes['4v4'])}
"""
        if common.SERVER is common.Server.MKW:
            msg += f"5) 6v6 - {len(self.votes['6v6'])}\n"
        msg += f"Winner: {format_[1]}\n\n"

        self.header_text = ""
        tier_text = "Tier " if common.SERVER is common.Server.MK8DX else "T"
        self.header_text += f"**Room {room.room_num} MMR: {room.avg_mmr} - {tier_text}{room.tier}** "
        msg += self.header_text + "\n"

        teams = []
        teams_per_room = int(12 / format_[0])
        for j in range(teams_per_room):
            team = Team(self.players[j * format_[0]:(j + 1) * format_[0]])
            teams.append(team)

        teams.sort(key=lambda team: team.avg_mmr, reverse=True)

        scoreboard_text = []

        for j in range(teams_per_room):
            team_text = f"`{j + 1}.` "
            team_names = ", ".join([p.lounge_name for p in teams[j].players])
            scoreboard_text.append(team_names)
            team_text += team_names
            team_text += f" ({int(teams[j].avg_mmr)} MMR)\n"
            msg += team_text
            self.teams_text += team_text

        if common.SERVER is common.Server.MK8DX:
            msg += f"\nTable: `/scoreboard`\n"

            msg += f"RandomBot Scoreboard: `/scoreboard {teams_per_room} {', '.join(scoreboard_text)}`\n\n"

        penalty_time = self.mogi.making_rooms_run_time + \
            timedelta(minutes=self.penalty_time)
        room_open_time = self.mogi.making_rooms_run_time
        potential_host_str = self.room.get_host_str()
        if potential_host_str == "":
            if common.SERVER.MK8DX:
                msg += f"\nRoom open at :{room_open_time.minute:02}, penalty at :{penalty_time.minute:02}. Good luck!"
            elif common.SERVER.MKW:
                msg += f"\nPenalty is {self.penalty_time.minute} minutes after the room opens. Good luck!"
        else:
            if common.SERVER.MK8DX:
                msg += f"\nRoom open at :{room_open_time.minute:02}, penalty at :{penalty_time.minute:02}. Good luck!"
            else:
                cur_time = datetime.now(timezone.utc)
                room_open_time = cur_time + timedelta(minutes=1)
                pen_time = cur_time + timedelta(minutes=self.penalty_time)
                msg += f"\nRoom open at :{room_open_time.minute:02}, penalty at :{pen_time.minute:02}. Good luck!"

        room.teams = teams

        self.found_winner = True
        await self.room_channel.send(msg)
        if common.CONFIG["USE_THREADS"]:
            new_thread_name = self.room_channel.name + f" - {tier_text}{room.tier}"
            await self.room_channel.edit(name=new_thread_name)

    async def find_winner(self):
        if not self.found_winner:
            # for some reason max function wasnt working... # It's because you were replacing it.
            most_votes = len(max(self.votes.values(), key=len))

            winners = []
            if len(self.votes["FFA"]) == most_votes:
                winners.append((1, "FFA"))
            if len(self.votes["2v2"]) == most_votes:
                winners.append((2, "2v2"))
            if len(self.votes["3v3"]) == most_votes:
                winners.append((3, "3v3"))
            if len(self.votes["4v4"]) == most_votes:
                winners.append((4, "4v4"))
            if common.SERVER is common.Server.MKW and len(self.votes["6v6"]) == most_votes:
                winners.append((6, "6v6"))

            winner = random.choice(winners)

            for curr_button in self.children:
                curr_button.disabled = True

            await self.make_teams(winner)

    def add_button(self, label, callback):
        button = discord.ui.Button(label=f"{label}: 0", custom_id=label)
        button.callback = callback
        self.add_item(button)

    async def general_vote_callback(self, interaction: discord.Interaction):
        if not self.found_winner:
            vote = interaction.data['custom_id']
            players_per_team = 1
            if vote != "FFA":
                players_per_team = int(vote[0])
            original_vote = None
            for vote_option, voter_ids in self.votes.items():
                if interaction.user.id in voter_ids:
                    original_vote = vote_option
                    voter_ids.remove(interaction.user.id)
            if original_vote != vote:  # They changed their vote or are a new voter
                self.votes[vote].append(interaction.user.id)
            if len(self.votes[vote]) == 6:
                self.found_winner = True  # This fixes a race condition
                await self.make_teams((players_per_team, vote))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self.votes[curr_button.custom_id])}"
                if self.found_winner:
                    curr_button.disabled = True
        await interaction.response.edit_message(view=self)


class JoinView(View):
    def __init__(self, room: Room, get_rating_from_discord_id, sub_range_mmr_allowance, bottom_room_num, is_restricted: Callable[[discord.User | discord.Member], bool] | None = None):
        super().__init__(timeout=1200)
        self.room = room
        self.get_rating_from_discord_id = get_rating_from_discord_id
        self.sub_range_mmr_allowance = sub_range_mmr_allowance
        self.bottom_room_num = bottom_room_num
        self.is_restricted = is_restricted

    @discord.ui.button(label="Join Room")
    async def button_callback(self, interaction: discord.Interaction, button):
        if self.is_restricted is not None and self.is_restricted(interaction.user):
            await interaction.response.send_message(
                "Players with the muted or restricted role cannot use the sub button.", ephemeral=True)
            return
        if interaction.user.id in self.room.get_player_list() + self.room.subs:
            await interaction.response.send_message(
                "You are already in this room.", ephemeral=True)
            return
        try:
            user_mmr = self.get_rating_from_discord_id(interaction.user.id)
            if isinstance(user_mmr, int):
                user_mmr = Player(None, "", user_mmr).adjusted_mmr
        except:
            await interaction.response.send_message(
                "MMR lookup for player has failed, please try again.", ephemeral=True)
            return

        mmr_high = 999999 if self.room.room_num == 1 else self.room.mmr_high
        mmr_low = -999999 if self.room.room_num == self.bottom_room_num else self.room.mmr_low
        if isinstance(user_mmr, int) and mmr_high + self.sub_range_mmr_allowance > user_mmr > mmr_low - self.sub_range_mmr_allowance:
            self.room.subs.append(interaction.user.id)
            button.disabled = True
            await interaction.response.edit_message(view=self)
            try:
                await self.room.assign_member_room_role(interaction.user)
            except RoleAddFailure as e:
                self.room.channel.send(str(e))
            mention = interaction.user.mention
            await self.room.channel.send(f"{mention} has joined the room.")
        else:
            await interaction.response.send_message(
                "You do not meet room requirements", ephemeral=True)


def get_tier_mkw(mmr: int, tier_info):
    for tier in tier_info:
        if (tier["minimum_mmr"] is None or mmr >= tier["minimum_mmr"]) and (
                tier["maximum_mmr"] is None or mmr <= tier["maximum_mmr"]):
            return tier["ladder_order"]


def get_tier_mk8dx(mmr: int):
    if mmr > 14000:
        return 'X'
    if mmr > 13000:
        return 'S'
    if mmr > 12000:
        return 'A'
    if mmr > 11000:
        return 'AB'
    if mmr > 10000:
        return 'B'
    if mmr > 9000:
        return 'BC'
    if mmr > 8000:
        return 'C'
    if mmr > 7000:
        return 'CD'
    if mmr > 6000:
        return 'D'
    if mmr > 5000:
        return 'DE'
    if mmr > 4000:
        return 'E'
    if mmr > 3000:
        return 'EF'
    if mmr > 2000:
        return 'F'
    if mmr > 1000:
        return 'FG'
    else:
        return 'G'
