import random
import discord
from datetime import datetime, timezone, timedelta
import time
from discord.ui import View
from typing import List


class Mogi:
    def __init__(self, sq_id: int, players_per_team: int, teams_per_room: int, mogi_channel: discord.TextChannel,
                 is_automated=False, start_time=None):
        self.started = False
        self.gathering = False
        self.making_rooms_run = False
        self.making_rooms_run_time = None
        self.sq_id = sq_id
        self.players_per_team = players_per_team
        self.teams_per_room = teams_per_room
        self.mogi_channel = mogi_channel
        self.teams: List[Team] = []
        self.rooms: List[Room] = []
        self.is_automated = is_automated
        self.start_time = start_time if is_automated else None

    @property
    def num_players(self):
        """Returns the total number of players in teams where all players have confirmed"""
        return self.num_teams * self.players_per_team

    @property
    def num_teams(self):
        """Returns the total number of teams where all players have confirmed"""
        return self.count_registered()

    @property
    def num_rooms(self):
        """Returns the number of rooms based on teams where all players have confirmed"""
        return self.num_teams // self.teams_per_room

    def check_player(self, member):
        for team in self.teams:
            if team.has_player(member):
                return team
        return None

    def count_registered(self):
        """Returns the number of teams that are registered"""
        return sum(1 for team in self.teams if team.is_registered())

    def confirmed_list(self):
        return [team for team in self.teams if team.is_registered()]

    def remove_id(self, squad_id: int):
        confirmed = self.confirmed_list()
        if squad_id < 1 or squad_id > len(confirmed):
            return None
        squad = confirmed[squad_id - 1]
        self.teams.remove(squad)
        return squad

    def is_room_thread(self, channel_id: int):
        for room in self.rooms:
            if room.thread.id == channel_id:
                return True
        return False

    def get_room_from_thread(self, channel_id: int):
        for room in self.rooms:
            if room.thread.id == channel_id:
                return room
        return None


class Room:
    def __init__(self, teams, room_num: int, thread: discord.Thread):
        self.teams = teams
        self.room_num = room_num
        self.thread = thread
        self.mmr_average = 0
        self.mmr_high = None
        self.mmr_low = None
        self.view = None
        self.finished = False

    def get_player_list(self):
        return [player.member.id for team in self.teams for player in team.players]


class Team:
    def __init__(self, players):
        self.players = players
        self.avg_mmr = sum([p.mmr for p in self.players]) / len(self.players)

    def recalc_avg(self):
        self.avg_mmr = sum([p.mmr for p in self.players]) / len(self.players)

    def is_registered(self):
        """Returns if all players on the team are registered"""
        return all(player.confirmed for player in self.players)

    def has_player(self, member):
        return any(player.member.id == member.id for player in self.players)

    def get_player(self, member):
        for player in self.players:
            if player.member.id == member.id:
                return player
        return None

    def get_first_player(self):
        return self.players[0]

    def sub_player(self, sub_out, sub_in):
        for i, player in enumerate(self.players):
            if player == sub_out:
                self.players[i] = sub_in
                self.recalc_avg()
                return

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
    def __init__(self, member, lounge_name, mmr):
        self.member = member
        self.lounge_name = lounge_name
        self.mmr = mmr
        self.confirmed = False
        self.score = 0


class VoteView(View):
    def __init__(self, players, thread, mogi: Mogi, tier_info):
        super().__init__()
        self.players = players
        self.thread = thread
        self.mogi = mogi
        self.header_text = ""
        self.teams_text = ""
        self.found_winner = False
        self.tier_info = tier_info
        self.votes = {"FFA": [],
                      "2v2": [],
                      "3v3": [],
                      "4v4": [],
                      "6v6": []
                      }

    async def make_teams(self, format_):
        self.mogi.making_rooms_run_time = datetime.now(timezone.utc)
        random.shuffle(self.players)

        room = self.mogi.get_room_from_thread(self.thread.id)

        msg = f"""**Poll Ended!**

1) FFA - {len(self.votes['FFA'])}
2) 2v2 - {len(self.votes['2v2'])}
3) 3v3 - {len(self.votes['3v3'])}
4) 4v4 - {len(self.votes['4v4'])}
5) 6v6 - {len(self.votes['6v6'])}
Winner: {format_[1]}

"""

        room_mmr = round(sum([p.mmr for p in self.players]) / 12)
        room.mmr_average = room_mmr
        self.header_text = f"**Room {room.room_num} MMR: {room_mmr} - T{get_tier(room_mmr, self.tier_info)}** "
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

        penalty_time = self.mogi.making_rooms_run_time + timedelta(minutes=8)
        room_open_time = self.mogi.making_rooms_run_time
        msg += f"Decide a host amongst yourselves; room open at :{room_open_time.minute:02}, penalty at :{penalty_time.minute:02}. Good luck!"

        room.teams = teams

        self.found_winner = True
        await self.thread.send(msg)

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
            if len(self.votes["6v6"]) == most_votes:
                winners.append((6, "6v6"))

            winner = random.choice(winners)

            for curr_button in self.children:
                curr_button.disabled = True

            await self.make_teams(winner)

    @discord.ui.button(label="FFA: 0", custom_id="FFA")
    async def one_button_callback(self, interaction, button):
        await self.general_vote_callback(interaction, 1, "FFA")

    @discord.ui.button(label="2v2: 0", custom_id="2v2")
    async def two_button_callback(self, interaction, button):
        await self.general_vote_callback(interaction, 2, "2v2")

    @discord.ui.button(label="3v3: 0", custom_id="3v3")
    async def three_button_callback(self, interaction, button):
        await self.general_vote_callback(interaction, 3, "3v3")

    @discord.ui.button(label="4v4: 0", custom_id="4v4")
    async def four_button_callback(self, interaction, button):
        await self.general_vote_callback(interaction, 4, "4v4")

    @discord.ui.button(label="6v6: 0", custom_id="6v6")
    async def six_button_callback(self, interaction, button):
        await self.general_vote_callback(interaction, 6, "6v6")

    async def general_vote_callback(self, interaction: discord.Interaction, players_per_team: int, vote: str):
        if not self.found_winner:
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
    def __init__(self, room: Room, get_mmr, bottom_room_num):
        super().__init__(timeout=1200)
        self.room = room
        self.get_mmr = get_mmr
        self.bottom_room_num = bottom_room_num

    @discord.ui.button(label="Join Room")
    async def button_callback(self, interaction, button):
        await interaction.response.defer()
        muted_role_id = 434887701662007296
        restricted_role_id = 797208908153618452
        if interaction.user.get_role(muted_role_id) or interaction.user.get_role(restricted_role_id):
            await interaction.followup.send(
                "Players with the muted or restricted role cannot use the sub button.", ephemeral=True)
            return
        if interaction.user.id in self.room.get_player_list():
            await interaction.followup.send(
                "You are already in this room.", ephemeral=True)
            return
        try:
            user_mmr = await self.get_mmr(interaction.user.id)
        except:
            await interaction.followup.send(
                "MMR lookup for player has failed, please try again.", ephemeral=True)
            return
        if self.room.room_num == 1:
            self.room.mmr_high = 999999
        if self.room.room_num == self.bottom_room_num:
            self.room.mmr_low = -999999
        if isinstance(user_mmr, int) and user_mmr < self.room.mmr_high + 500 and user_mmr > self.room.mmr_low - 500:
            button.disabled = True
            await interaction.followup.edit_message(interaction.message.id, view=self)
            mention = interaction.user.mention
            await self.room.thread.send(f"{mention} has joined the room.")
        else:
            await interaction.followup.send(
                "You do not meet room requirements", ephemeral=True)


def get_tier(mmr: int, tier_info):
    for tier in tier_info:
        if (tier["minimum_mmr"] is None or mmr >= tier["minimum_mmr"]) and (
                tier["maximum_mmr"] is None or mmr <= tier["maximum_mmr"]):
            return tier["ladder_order"]
