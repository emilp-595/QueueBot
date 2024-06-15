import random
import discord
from datetime import datetime, timezone, timedelta
import time
from discord.ui import View


class Mogi:
    def __init__(self, sq_id: int, size: int, mogi_channel: discord.TextChannel,
                 is_automated=False, start_time=None):
        self.started = False
        self.gathering = False
        self.making_rooms_run = False
        self.sq_id = sq_id
        self.size = size
        self.mogi_channel = mogi_channel
        self.teams = []
        self.rooms = []
        self.is_automated = is_automated
        if not is_automated:
            self.start_time = None
        else:
            self.start_time = start_time

    def check_player(self, member):
        for team in self.teams:
            if team.has_player(member):
                return team
        return None

    def count_registered(self):
        count = 0
        for team in self.teams:
            if team.is_registered():
                count += 1
        return count

    def confirmed_list(self):
        confirmed = []
        for team in self.teams:
            if team.is_registered():
                confirmed.append(team)
        return confirmed

    def remove_id(self, squad_id: int):
        confirmed = self.confirmed_list()
        if squad_id < 1 or squad_id > len(confirmed):
            return None
        squad = confirmed[squad_id-1]
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
        for player in self.players:
            if player.confirmed is False:
                return False
        return True

    def has_player(self, member):
        for player in self.players:
            if player.member.id == member.id:
                return True
        return False

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
        count = 0
        for player in self.players:
            if player.confirmed:
                count += 1
        return count

    def get_unconfirmed(self):
        unconfirmed = []
        for player in self.players:
            if not player.confirmed:
                unconfirmed.append(player)
        return unconfirmed

    def __lt__(self, other):
        if self.avg_mmr < other.avg_mmr:
            return True
        if self.avg_mmr > other.avg_mmr:
            return False

    def __gt__(self, other):
        return other.__lt__(self)

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
    def __init__(self, players, thread, mogi, tier_info):
        super().__init__()
        self.players = players
        self.thread = thread
        self.mogi = mogi
        self.header_text = ""
        self.teams_text = ""
        self.found_winner = False
        self.tier_info = tier_info
        self.__setattr__("FFA", [])
        self.__setattr__("2v2", [])
        self.__setattr__("3v3", [])
        self.__setattr__("4v4", [])
        self.__setattr__("6v6", [])

    def __getitem__(self, key):
        return getattr(self, key)

    async def make_teams(self, format):
        random.shuffle(self.players)

        room = self.mogi.get_room_from_thread(self.thread.id)

        msg = "**Poll Ended!** \n\n"
        msg += f"1) FFA - {len(self['FFA'])}\n"
        msg += f"2) 2v2 - {len(self['2v2'])}\n"
        msg += f"3) 3v3 - {len(self['3v3'])}\n"
        msg += f"4) 4v4 - {len(self['4v4'])}\n"
        msg += f"5) 6v6 - {len(self['6v6'])}\n"
        msg += f"Winner: {format[1]}\n\n"

        room_mmr = round(sum([p.mmr for p in self.players]) / 12)
        room.mmr_average = room_mmr
        self.header_text = f"**Room {room.room_num} MMR: {room_mmr} - T{get_tier(room_mmr, self.tier_info)}** "
        msg += self.header_text
        msg += "\n"

        teams = []
        teams_per_room = int(12 / format[0])
        for j in range(teams_per_room):
            team = Team(self.players[j*format[0]:(j+1)*format[0]])
            teams.append(team)

        teams.sort(key=lambda team: team.avg_mmr, reverse=True)

        scoreboard_text = []

        for j in range(teams_per_room):
            team_text = f"`{j+1}.` "
            team_names = ", ".join([p.lounge_name for p in teams[j].players])
            scoreboard_text.append(team_names)
            team_text += team_names
            team_text += f" ({int(teams[j].avg_mmr)} MMR)\n"
            msg += team_text
            self.teams_text += team_text

        minutes = self.mogi.start_time.minute
        pen = minutes + 8

        if pen > 60:
            pen -= 60

        if pen < 10:
            pen = '0' + str(pen)
        if minutes < 10:
            minutes = '0' + str(minutes)
        msg += "Decide a host amongst yourselves; room open at :{}, penalty at :{}. Good luck!".format(minutes, pen)

        room.teams = teams

        self.found_winner = True
        await self.thread.send(msg)

    async def find_winner(self):
        if not self.found_winner:
            # for some reason max function wasnt working...
            max = 0
            if len(self["FFA"]) > max:
                max = len(self["FFA"])
            if len(self["2v2"]) > max:
                max = len(self["2v2"])
            if len(self["3v3"]) > max:
                max = len(self["3v3"])
            if len(self["4v4"]) > max:
                max = len(self["4v4"])
            if len(self["6v6"]) > max:
                max = len(self["6v6"])

            winners = []

            if len(self["FFA"]) == max:
                winners.append((1, "FFA"))
            if len(self["2v2"]) == max:
                winners.append((2, "2v2"))
            if len(self["3v3"]) == max:
                winners.append((3, "3v3"))
            if len(self["4v4"]) == max:
                winners.append((4, "4v4"))
            if len(self["6v6"]) == max:
                winners.append((6, "6v6"))

            winner = random.choice(winners)

            for curr_button in self.children:
                curr_button.disabled = True

            await self.make_teams(winner)

    @discord.ui.button(label="FFA: 0", custom_id="FFA")
    async def one_button_callback(self, interaction, button):
        if not self.found_winner:
            if interaction.user.id in self["FFA"]:
                self["FFA"].remove(interaction.user.id)
            else:
                if interaction.user.id in self["2v2"]:
                    self["2v2"].remove(interaction.user.id)
                if interaction.user.id in self["3v3"]:
                    self["3v3"].remove(interaction.user.id)
                if interaction.user.id in self["4v4"]:
                    self["4v4"].remove(interaction.user.id)
                if interaction.user.id in self["6v6"]:
                    self["6v6"].remove(interaction.user.id)
                self["FFA"].append(interaction.user.id)
            if len(self["FFA"]) == 6:
                await self.make_teams((1, "FFA"))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self[curr_button.custom_id])}"
                if len(self["FFA"]) == 6:
                    curr_button.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="2v2: 0", custom_id="2v2")
    async def two_button_callback(self, interaction, button):
        if not self.found_winner:
            if interaction.user.id in self["2v2"]:
                self["2v2"].remove(interaction.user.id)
            else:
                if interaction.user.id in self["FFA"]:
                    self["FFA"].remove(interaction.user.id)
                if interaction.user.id in self["3v3"]:
                    self["3v3"].remove(interaction.user.id)
                if interaction.user.id in self["4v4"]:
                    self["4v4"].remove(interaction.user.id)
                if interaction.user.id in self["6v6"]:
                    self["6v6"].remove(interaction.user.id)
                self["2v2"].append(interaction.user.id)
            if len(self["2v2"]) == 6:
                await self.make_teams((2, "2v2"))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self[curr_button.custom_id])}"
                if len(self["2v2"]) == 6:
                    curr_button.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="3v3: 0", custom_id="3v3")
    async def three_button_callback(self, interaction, button):
        if not self.found_winner:
            if interaction.user.id in self["3v3"]:
                self["3v3"].remove(interaction.user.id)
            else:
                if interaction.user.id in self["FFA"]:
                    self["FFA"].remove(interaction.user.id)
                if interaction.user.id in self["2v2"]:
                    self["2v2"].remove(interaction.user.id)
                if interaction.user.id in self["4v4"]:
                    self["4v4"].remove(interaction.user.id)
                if interaction.user.id in self["6v6"]:
                    self["6v6"].remove(interaction.user.id)
                self["3v3"].append(interaction.user.id)
            if len(self["3v3"]) == 6:
                await self.make_teams((3, "3v3"))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self[curr_button.custom_id])}"
                if len(self["3v3"]) == 6:
                    curr_button.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="4v4: 0", custom_id="4v4")
    async def four_button_callback(self, interaction, button):
        if not self.found_winner:
            if interaction.user.id in self["4v4"]:
                self["4v4"].remove(interaction.user.id)
            else:
                if interaction.user.id in self["FFA"]:
                    self["FFA"].remove(interaction.user.id)
                if interaction.user.id in self["2v2"]:
                    self["2v2"].remove(interaction.user.id)
                if interaction.user.id in self["3v3"]:
                    self["3v3"].remove(interaction.user.id)
                if interaction.user.id in self["6v6"]:
                    self["6v6"].remove(interaction.user.id)
                self["4v4"].append(interaction.user.id)
            if len(self["4v4"]) == 6:
                await self.make_teams((4, "4v4"))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self[curr_button.custom_id])}"
                if len(self["4v4"]) == 6:
                    curr_button.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="6v6: 0", custom_id="6v6")
    async def six_button_callback(self, interaction, button):
        if not self.found_winner:
            if interaction.user.id in self["6v6"]:
                self["6v6"].remove(interaction.user.id)
            else:
                if interaction.user.id in self["FFA"]:
                    self["FFA"].remove(interaction.user.id)
                if interaction.user.id in self["2v2"]:
                    self["2v2"].remove(interaction.user.id)
                if interaction.user.id in self["3v3"]:
                    self["3v3"].remove(interaction.user.id)
                if interaction.user.id in self["4v4"]:
                    self["4v4"].remove(interaction.user.id)
                self["6v6"].append(interaction.user.id)
            if len(self["6v6"]) == 6:
                await self.make_teams((6, "6v6"))
            for curr_button in self.children:
                curr_button.label = f"{curr_button.custom_id}: {len(self[curr_button.custom_id])}"
                if len(self["6v6"]) == 6:
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
        if (tier["minimum_mmr"] is None or mmr >= tier["minimum_mmr"]) and (tier["maximum_mmr"] is None or mmr <= tier["maximum_mmr"]):
            return tier["ladder_order"]
