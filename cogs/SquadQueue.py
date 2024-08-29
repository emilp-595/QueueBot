from __future__ import annotations

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone, timedelta
import pytz
import time
import json

import common
import mogi_objects
from common import divide_chunks
import mmr
from mogi_objects import Mogi, Team, Player, Room, VoteView, JoinView
import asyncio
from collections import defaultdict
from typing import Optional, Dict, List, Tuple
import traceback
import os
import dill


headers = {'Content-type': 'application/json'}

# Scheduled_Event = collections.namedtuple('Scheduled_Event', 'size time started mogi_channel')

cooldowns = defaultdict(int)
STAFF_SETTINGS_PKL = "./settings_data/staff_settings.pkl"


def is_restricted(user: discord.User | discord.Member) -> bool:
    muted_role_id = common.CONFIG.get("muted_role_id")
    restricted_role_id = common.CONFIG.get("restricted_role_id")
    return (muted_role_id is not None and user.get_role(muted_role_id)) or (
        restricted_role_id is not None and user.get_role(restricted_role_id))


def basic_threshold_players_allowed(
        players: List[Player],
        threshold: int) -> bool:
    """Returns True if the highest player's rating minus the lowest player's rating is below the given threshold"""
    sorted_players = sorted(players, key=lambda p: p.mmr)
    return (sorted_players[-1].adjusted_mmr -
            sorted_players[0].adjusted_mmr) <= threshold


def mkw_players_allowed(players: List[Player], threshold: int) -> bool:
    """Returns true if the given list of players would be allowed to play together"""
    return basic_threshold_players_allowed(players, threshold)


def mk8dx_players_allowed(players: List[Player], threshold: int) -> bool:
    """Returns true if the given list of players would be allowed to play together"""
    return basic_threshold_players_allowed(players, threshold)


@app_commands.guild_only()
class SettingsGroup(app_commands.Group):
    def __init__(self, squad_queue: SquadQueue, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.squad_queue: SquadQueue = squad_queue

    @app_commands.guild_only()
    @app_commands.command(name="mmr_threshold",
                          description="Change the maximum MMR gap allowed for a room.")
    @app_commands.describe(
        mmr_range="The maximum range allowed for a room. Rooms with a range larger than this will be cancelled.")
    async def mmr_threshold(self, interaction: discord.Interaction, mmr_range: int):
        self.squad_queue.room_mmr_threshold = mmr_range
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"MMR Threshold for Queue Rooms has been modified to {mmr_range} MMR.")

    @app_commands.command(name="time_between_events",
                          description="Change the amount of time between each queue opening.")
    @app_commands.describe(event_time="Amount of time between each queue's opening.")
    @app_commands.guild_only()
    async def time_between_events(self, interaction: discord.Interaction, event_time: int):
        if event_time > 15:
            self.squad_queue.QUEUE_OPEN_TIME = timedelta(minutes=event_time)
            self.squad_queue.JOINING_TIME = timedelta(
                minutes=event_time) - self.squad_queue.EXTENSION_TIME
            self.squad_queue.dump_staff_settings()
            await interaction.response.send_message(
                f"The amount of time for each mogi has been changed to {event_time} minutes.")
        else:
            await interaction.response.send_message("Please enter a number of minutes greater than 15.")

    @app_commands.guild_only()
    @app_commands.command(
        name="low_mmr_floor",
        description="Players below this rating will be considered this rating for matchmaking/sub purposes.")
    @app_commands.describe(
        rating="Players below this rating will be considered this rating for matchmaking/sub purposes.")
    async def low_mmr_floor(self, interaction: discord.Interaction, rating: int):
        common.CONFIG["MATCHMAKING_BOTTOM_MMR"] = rating
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"Players below {rating} MMR will be considered {rating} MMR for matchmaking and subbing in purposes.")

    @app_commands.guild_only()
    @app_commands.command(
        name="high_mmr_ceiling",
        description="Players above this rating will be considered this rating for matchmaking/sub purposes.")
    @app_commands.describe(
        rating="Players above this rating will be considered this rating for matchmaking/sub purposes.")
    async def high_mmr_ceiling(self, interaction: discord.Interaction, rating: int):
        common.CONFIG["MATCHMAKING_TOP_MMR"] = rating
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"Players above {rating} MMR will be considered {rating} MMR for matchmaking and subbing in purposes.")

    @app_commands.guild_only()
    @app_commands.command(name="first_event_open_time",
                          description="Use to shift schedule.")
    @app_commands.describe(
        minutes="The number of minutes AFTER 0:00 UTC that your first queue of the day would open.")
    async def first_event_open_time(self, interaction: discord.Interaction, minutes: int):
        old_amount = common.CONFIG["FIRST_EVENT_TIME"]
        shift = minutes - old_amount
        common.CONFIG["FIRST_EVENT_TIME"] = minutes
        self.squad_queue.FIRST_EVENT_TIME = datetime.combine(
            datetime.utcnow().date(),
            datetime.min.time(),
            tzinfo=timezone.utc) + timedelta(
            minutes=common.CONFIG["FIRST_EVENT_TIME"])
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"Changed first queue of the day's open time to {minutes} minutes after 0:00 UTC, which will shift all events by {shift} minutes.")

    @app_commands.guild_only()
    @app_commands.command(
        name="sub_range_allowance",
        description="Buffer from the lowest and highest player that someone's rating could be to be allowed to sub in.")
    @app_commands.describe(
        buffer="The buffer. E.g. 500 buffer, low mmr 2000, high mmr 6000, so allowed range 1500-6500")
    async def sub_range_allowance(self, interaction: discord.Interaction, buffer: int):
        self.squad_queue.SUB_RANGE_MMR_ALLOWANCE = buffer
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"Players rated higher than {buffer} below a room's lowest player and lower than {buffer} above a room's highest player will be allowed to sub into the room.")

    @app_commands.guild_only()
    @app_commands.command(name="placement_rating",
                          description="Rating for placement players when they queue.")
    @app_commands.describe(rating="The placement players' rating.")
    async def placement_rating(self, interaction: discord.Interaction, rating: int):
        self.squad_queue.PLACEMENT_PLAYER_MMR = rating
        common.CONFIG["PLACEMENT_PLAYER_MMR"] = rating
        self.squad_queue.dump_staff_settings()
        await interaction.response.send_message(f"Placement players' rating will be {rating} MMR when they queue.")


class SquadQueue(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot

        self.bot_startup_time = datetime.now(timezone.utc)

        self.is_production = bot.config["is_production"]

        self.next_event: Mogi = None

        self.ongoing_event: Mogi = None

        self.old_events: List[Mogi] = []

        self.sq_times: List[datetime] = []

        self.forced_format_times: List[Tuple[datetime, str]] = []

        self.forced_format_order: List[str] = bot.config["FORCED_FORMAT_ORDER"]

        self.queues_between_forced_format_queue: int | None = bot.config[
            "QUEUES_BETWEEN_FORCED_FORMAT_QUEUE"]

        # Parameters for tracking if we should send an extension message or not
        self.last_extension_message_timestamp = datetime.now(
            timezone.utc).replace(second=0, microsecond=0)
        self.cur_extension_message = None

        self._scheduler_task = self.sqscheduler.start()
        self._msgqueue_task = self.send_queued_messages.start()
        self._list_task = self.list_task.start()
        self._end_mogis_task = self.delete_old_mogis.start()

        self.msg_queue = {}

        self.list_messages = []

        self.format_messages = []

        self.LAUNCH_NEW_EVENTS = True

        self.GUILD: discord.Guild = None

        self.MOGI_CHANNEL = None

        self.SUB_CHANNEL = None

        self.LIST_CHANNEL = None

        self.HISTORY_CHANNEL = None

        self.SCHEDULE_CHANNEL = None

        self.GENERAL_CHANNEL: discord.TextChannel = None

        self.URL = bot.config["url"]

        self.PLACEMENT_PLAYER_MMR = self.bot.config["PLACEMENT_PLAYER_MMR"]

        self.ROOM_JOIN_PENALTY_TIME = self.bot.config["ROOM_JOIN_PENALTY_TIME"]

        self.MOGI_LIFETIME = bot.config["MOGI_LIFETIME"]

        self.SUB_RANGE_MMR_ALLOWANCE = bot.config["SUB_RANGE_MMR_ALLOWANCE"]

        self.SUB_MESSAGE_LIFETIME_SECONDS = bot.config["SUB_MESSAGE_LIFETIME_SECONDS"]

        self.room_mmr_threshold = bot.config["ROOM_MMR_THRESHOLD"]

        self.TRACK_TYPE = bot.config["track_type"]

        self.TIER_INFO = []

        # Time between each event queue opening
        self.QUEUE_OPEN_TIME = timedelta(minutes=bot.config["QUEUE_OPEN_TIME"])

        # number of minutes after QUEUE_OPEN_TIME that teams can join the mogi
        self.JOINING_TIME = timedelta(minutes=bot.config["JOINING_TIME"])

        self.DISPLAY_OFFSET_MINUTES = timedelta(
            minutes=bot.config["DISPLAY_OFFSET_MINUTES"])

        # number of minutes after JOINING_TIME for any potential extra teams to
        # join
        self.EXTENSION_TIME = timedelta(minutes=bot.config["EXTENSION_TIME"])

        assert (
            self.QUEUE_OPEN_TIME >= self.JOINING_TIME +
            self.EXTENSION_TIME)

        # This is the first event of the day's time. However, this isn't the
        # first event the bot will run. This is literally the time of the first
        # event in a daily schedule.
        self.FIRST_EVENT_TIME = datetime.combine(datetime.utcnow().date(), datetime.min.time(
        ), tzinfo=timezone.utc) + timedelta(minutes=bot.config["FIRST_EVENT_TIME"])

        self.FORCED_FORMAT_FIRST_EVENT = datetime(
            2024, 8, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=bot.config["FIRST_EVENT_TIME"]) \
            + timedelta(hours=bot.config["FORCED_FORMAT_HOURLY_OFFSET"])

        self.FORCED_FORMAT_ORDER_OFFSET = bot.config["FORCED_FORMAT_ORDER_OFFSET"]

        self.FORCED_FORMAT_AUTOSCHEDULE_AMOUNT = bot.config["FORCED_FORMAT_AUTOSCHEDULE_AMOUNT"]

        # If we're testing, set the time to the current time so we start a new
        # event immediately
        if not self.is_production:
            self.FIRST_EVENT_TIME = datetime.now(
                timezone.utc).replace(second=0, microsecond=0)

        with open('./timezones.json', 'r') as cjson:
            self.timezones = json.load(cjson)

        # The following dictionary will be populated using the config's staff_helper_roles list. Players can
        # call these roles into their room/thread using the /staff role command
        # These will be refreshed every 24 hours to ensure that the correct
        # name displays for the options
        self.helper_staff_roles: Dict[str, discord.Role] = {}

        self.ratings = mmr.Ratings()

        self.load_staff_settings()

    def load_staff_settings(self, view_only=False):
        if os.path.isfile(STAFF_SETTINGS_PKL):
            try:
                with open(STAFF_SETTINGS_PKL, 'rb') as f:
                    settings_dict = dill.load(f)
                    if view_only:
                        print(settings_dict)
                        return
                    if "ROOM_MMR_THRESHOLD" in settings_dict:
                        self.room_mmr_threshold = settings_dict["ROOM_MMR_THRESHOLD"]
                    if "QUEUE_OPEN_TIME" in settings_dict:
                        self.QUEUE_OPEN_TIME = settings_dict["QUEUE_OPEN_TIME"]
                    if "JOINING_TIME" in settings_dict:
                        self.JOINING_TIME = settings_dict["JOINING_TIME"]
                    if "MATCHMAKING_BOTTOM_MMR" in settings_dict:
                        common.CONFIG["MATCHMAKING_BOTTOM_MMR"] = settings_dict["MATCHMAKING_BOTTOM_MMR"]
                    if "MATCHMAKING_TOP_MMR" in settings_dict:
                        common.CONFIG["MATCHMAKING_TOP_MMR"] = settings_dict["MATCHMAKING_TOP_MMR"]
                    if "FIRST_EVENT_TIME" in settings_dict:
                        common.CONFIG["FIRST_EVENT_TIME"] = settings_dict["FIRST_EVENT_TIME"]
                        self.FIRST_EVENT_TIME = datetime.combine(datetime.utcnow().date(), datetime.min.time(
                        ), tzinfo=timezone.utc) + timedelta(minutes=settings_dict["FIRST_EVENT_TIME"])
                    if "SUB_RANGE_MMR_ALLOWANCE" in settings_dict:
                        self.SUB_RANGE_MMR_ALLOWANCE = settings_dict["SUB_RANGE_MMR_ALLOWANCE"]
                    if "PLACEMENT_PLAYER_MMR" in settings_dict:
                        self.PLACEMENT_PLAYER_MMR = settings_dict["PLACEMENT_PLAYER_MMR"]
                        common.CONFIG["PLACEMENT_PLAYER_MMR"] = settings_dict["PLACEMENT_PLAYER_MMR"]
            except BaseException:
                print(traceback.format_exc())

    def dump_staff_settings(self):
        settings_dict = {
            "ROOM_MMR_THRESHOLD": self.room_mmr_threshold,
            "QUEUE_OPEN_TIME": self.QUEUE_OPEN_TIME,
            "JOINING_TIME": self.JOINING_TIME,
            "SUB_RANGE_MMR_ALLOWANCE": self.SUB_RANGE_MMR_ALLOWANCE,
            "MATCHMAKING_BOTTOM_MMR": common.CONFIG["MATCHMAKING_BOTTOM_MMR"],
            "MATCHMAKING_TOP_MMR": common.CONFIG["MATCHMAKING_TOP_MMR"],
            "FIRST_EVENT_TIME": common.CONFIG["FIRST_EVENT_TIME"],
            "PLACEMENT_PLAYER_MMR": self.PLACEMENT_PLAYER_MMR}
        try:
            with open(STAFF_SETTINGS_PKL, 'wb') as f:
                dill.dump(settings_dict, f)
        except BaseException:
            print(traceback.format_exc())

    @commands.Cog.listener()
    async def on_ready(self):
        self.GUILD = self.bot.get_guild(self.bot.config["guild_id"])
        self.MOGI_CHANNEL = self.bot.get_channel(
            self.bot.config["queue_join_channel"])
        self.SUB_CHANNEL = self.bot.get_channel(
            self.bot.config["queue_sub_channel"])
        self.LIST_CHANNEL = self.bot.get_channel(
            self.bot.config["queue_list_channel"])
        self.HISTORY_CHANNEL = self.bot.get_channel(
            self.bot.config["queue_history_channel"])
        self.GENERAL_CHANNEL = self.bot.get_channel(
            self.bot.config["queue_general_channel"])
        schedule_channel_id = self.bot.config["queue_schedule_channel"]
        if schedule_channel_id:
            self.SCHEDULE_CHANNEL = self.bot.get_channel(
                schedule_channel_id)
        if common.SERVER is common.Server.MKW:
            await self.get_ladder_info()

        def is_queuebot(m: discord.Message):
            return m.author.id == self.bot.user.id
        purge_after_amt = 365 if self.is_production else 1
        purge_after = datetime.now(timezone.utc) - \
            timedelta(days=purge_after_amt)
        try:
            await self.LIST_CHANNEL.purge(check=is_queuebot, after=purge_after)
        except BaseException:
            print("Purging List channel failed", flush=True)
            print(traceback.format_exc())
        try:
            await self.SUB_CHANNEL.purge(check=is_queuebot, after=purge_after)
        except BaseException:
            print("Purging Sub channel failed", flush=True)
            print(traceback.format_exc())
        try:
            if schedule_channel_id:
                await self.SCHEDULE_CHANNEL.purge(check=is_queuebot, after=purge_after)
                if len(self.forced_format_order) > 0 and self.queues_between_forced_format_queue:
                    await self.autoschedule_forced_format_times()
        except BaseException:
            print("Purging Schedule channel failed", flush=True)
            print(traceback.format_exc())

        settings_group = SettingsGroup(
            self,
            name="settings",
            description="Change bot configuration. Staff use only.")
        self.bot.tree.add_command(settings_group)
        print(f"Server - {self.GUILD}", flush=True)
        print(f"Join Channel - {self.MOGI_CHANNEL}", flush=True)
        print(f"Sub Channel - {self.SUB_CHANNEL}", flush=True)
        print(f"List Channel - {self.LIST_CHANNEL}", flush=True)
        print(f"History Channel - {self.HISTORY_CHANNEL}", flush=True)
        print(f"General Channel - {self.GENERAL_CHANNEL}", flush=True)
        print(f"Schedule Channel - {self.SCHEDULE_CHANNEL}", flush=True)
        print("Ready!", flush=True)
        self.refresh_ratings.start()
        self.refresh_helper_roles.start()
        self.check_room_threads_task.start()
        if not common.CONFIG["USE_THREADS"]:
            self.maintain_roles.start()

    async def lockdown(self, channel: discord.TextChannel):
        # everyone_perms = channel.permissions_for(channel.guild.default_role)
        # if not everyone_perms.send_messages:
        #     return
        try:
            overwrite = channel.overwrites_for(channel.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
            await channel.send("Locked down " + channel.mention)
        except Exception as e:
            print(traceback.format_exc())

    async def unlockdown(self, channel: discord.TextChannel):
        # everyone_perms = channel.permissions_for(channel.guild.default_role)
        # if everyone_perms.send_messages:
        #     return
        overwrite = channel.overwrites_for(channel.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
        await channel.send("Unlocked " + channel.mention)

    # either adds a message to the message queue or sends it, depending on
    # server settings
    async def queue_or_send(self, ctx, msg, delay=0):
        if ctx.bot.config["queue_messages"] is True:
            if ctx.channel not in self.msg_queue.keys():
                self.msg_queue[ctx.channel] = []
            self.msg_queue[ctx.channel].append(msg)
        else:
            sendmsg = await ctx.send(msg)
            if delay > 0:
                await sendmsg.delete(delay=delay)

    # Support for both server to implement their own algorithm for a player list being allowed
    # For now, both servers have a simple rating range check that a list of players must meet,
    # but each server could implement more complex checks on a given list of
    # players.
    def allowed_players_check(self, players: List[Player]):
        if common.SERVER is common.Server.MKW:
            return mkw_players_allowed(players, self.room_mmr_threshold)
        elif common.SERVER is common.Server.MK8DX:
            return mk8dx_players_allowed(players, self.room_mmr_threshold)

    # goes thru the msg queue for each channel and combines them
    # into as few messsages as possible, then sends them
    @tasks.loop(seconds=2)
    async def send_queued_messages(self):
        try:
            for channel in self.msg_queue.keys():
                channel_queue = self.msg_queue[channel]
                sentmsgs = []
                msg = ""
                for i in range(len(channel_queue) - 1, -1, -1):
                    msg = channel_queue.pop(i) + "\n" + msg
                    if len(msg) > 1500:
                        sentmsgs.append(msg)
                        msg = ""
                if len(msg) > 0:
                    sentmsgs.append(msg)
                for i in range(len(sentmsgs) - 1, -1, -1):
                    await channel.send(sentmsgs[i])
        except Exception as e:
            print(traceback.format_exc())

    def get_mogi(self, ctx: discord.Interaction |
                 discord.ext.commands.context.Context) -> Mogi | None:
        if self.ongoing_event is None:
            return None
        if self.ongoing_event.mogi_channel.id == ctx.channel.id:
            return self.ongoing_event

    def is_staff(self, member: discord.Member):
        return any(member.get_role(staff_role)
                   for staff_role in self.bot.config["admin_roles"])

    async def is_started(self, ctx, mogi: Mogi):
        if not mogi.started:
            await ctx.send("Mogi has not been started yet... type !start")
        return mogi.started

    async def is_gathering(self, ctx, mogi: Mogi):
        if not mogi.gathering:
            await ctx.send("Mogi is closed; players cannot join or drop from the event")
        return mogi.gathering

    @app_commands.command(name="extend")
    @app_commands.guild_only()
    async def extend(self, interaction: discord.Interaction, minutes: int):
        """Extend the queue

        Parameters
        -----------
        minutes: int
            The number of minutes to add to the extension time. Can be negative.

        Staff use only.
        """
        mogi = self.get_mogi(interaction)
        if mogi is None or not mogi.started or not mogi.gathering:
            await interaction.response.send_message("Queue has not started yet.")
            return
        mogi.additional_extension += timedelta(minutes=minutes)
        await interaction.response.send_message(f"Extended queue by an additional {minutes} minute(s).")

    @app_commands.command(name="ch")
    @app_commands.guild_only()
    async def can_host(self, interaction: discord.Interaction):
        """Join a mogi as a host"""
        await self.join_queue(interaction, host=True)

    @app_commands.command(name="c")
    @app_commands.guild_only()
    async def can(self, interaction: discord.Interaction):
        """Join a mogi"""
        await self.join_queue(interaction)

    async def join_queue(self, interaction: discord.Interaction, host=False):
        member = interaction.user
        if not self.is_production:
            if common.SERVER is common.Server.MK8DX:
                # is actually a user and not a member
                member = await self.bot.fetch_user(318637887597969419)
            elif common.SERVER is common.Server.MKW:
                # member = await self.bot.fetch_user(82862780591378432)
                pass
        mogi = self.get_mogi(interaction)
        if mogi is None or not mogi.started or not mogi.gathering:
            await interaction.response.send_message("Queue has not started yet.")
            return

        player_team = mogi.check_player(member)
        player = None if player_team is None else player_team.get_player(
            member)

        if player is not None:
            original_host_status = player.host
            player.host = host
            # The player is already signed up, but they might be changing to a host or non-host. Begin checks:
            # The player was queued as host, and they queued again as a host
            if original_host_status and host:
                await interaction.response.send_message(f"{interaction.user.mention} is already signed up as a host.")
            # The player was queued as host, and they queued again as a
            # non-host
            elif original_host_status and not host:
                await interaction.response.send_message(f"{interaction.user.mention} has changed to a non-host.")
            # The player was not queued as host, but they are changing to a
            # host
            elif not original_host_status and host:
                await interaction.response.send_message(f"{interaction.user.mention} has changed to a host.")
            # The player was not queued as host and did not change to a host
            elif not original_host_status and not host:
                await interaction.response.send_message(f"{interaction.user.mention} is already signed up.")
            return

        # FIRST look up the player - sometimes MK8DX bots add placement role to non placement players,
        # so this will check the leaderboard first
        players = self.ratings.get_rating([member])

        msg = ""
        # If the no rating was found...
        if len(players) == 0 or players[0] is None:
            # ... check for placement discord role ID:
            placement_role_id = self.bot.config["placement_role_id"]
            if member.get_role(placement_role_id):
                starting_player_mmr = self.PLACEMENT_PLAYER_MMR
                players = [
                    Player(member, member.display_name, starting_player_mmr)]
                msg += f"{players[0].lounge_name} is assumed to be a new player and will be playing this mogi " \
                       f"with a starting MMR of {starting_player_mmr}.  If you believe this is a mistake, " \
                       f"please contact a staff member for help.\n"
            # ...if discord user doesn't have placement role ID, send error to Discord
            else:
                msg = f"{interaction.user.mention} fetch for MMR has failed and joining the queue was " \
                      f"unsuccessful.  Please try again.  If the problem continues then contact a staff member " \
                      f"for help."
                await interaction.response.send_message(msg)
                return

        players[0].confirmed = True
        players[0].host = host
        squad = Team(players)
        mogi.teams.append(squad)
        host_str = " as a host " if host else " "
        format_str = f"the {mogi.format} " if mogi.format else ""
        msg += f"{players[0].lounge_name} joined {format_str}queue{host_str}closing at {discord.utils.format_dt(mogi.start_time)}, `[{mogi.count_registered()} players]`"
        if common.SERVER is common.Server.MKW:
            if players[0].is_matchmaking_mmr_adjusted:
                msg += f"\nPlayer considered {players[0].adjusted_mmr} MMR for matchmaking purposes."

        event_status_launched = self.check_close_event_change()
        try:
            await interaction.response.send_message(msg)
        finally:
            if event_status_launched:
                await self.launch_mogi()

    @app_commands.command(name="d")
    @app_commands.guild_only()
    async def drop(self, interaction: discord.Interaction):
        """Remove user from mogi"""
        mogi = self.get_mogi(interaction)
        if mogi is None or not mogi.started or not mogi.gathering:
            await interaction.response.send_message("Queue has not started yet.")
            return

        member = interaction.user
        squad = mogi.check_player(member)
        if squad is None:
            await interaction.response.send_message(f"{member.display_name} is not currently in this event; type `/c` or `/ch` to join")
            return
        mogi.teams.remove(squad)
        msg = "Removed "
        msg += ", ".join([p.lounge_name for p in squad.players])
        msg += f" from the mogi {discord.utils.format_dt(mogi.start_time, style='R')}"
        msg += f", `[{mogi.count_registered()} players]`"

        event_status_launched = self.check_close_event_change()
        try:
            await interaction.response.send_message(msg)
        finally:
            if event_status_launched:
                await self.launch_mogi()

    @app_commands.command(name="host")
    @app_commands.guild_only()
    async def host(self, interaction: discord.Interaction):
        """Display who is the host"""
        room, _ = self.find_room_by_interaction(interaction)
        if room is None:
            await interaction.response.send_message(
                f"More than {self.MOGI_LIFETIME} minutes have passed since mogi start, so this mogi has ended.",
                ephemeral=True)
            return
        host_str = room.get_host_str()
        if host_str == "":
            host_str = "No one in this room queued as host! Ask someone who the host is."
        await interaction.response.send_message(host_str)

    def find_room_by_interaction(
            self, interaction: discord.Interaction) -> Tuple[Room | None, int]:
        """Given an interaction, returns the Room and room number associated with the interaction.
        Searches current and old, undeleted events. If the interaction is not associated with a Room,
        (None, 1) is returned."""
        all_mogis = list(self.old_events)
        if self.ongoing_event is not None:
            all_mogis.insert(0, self.ongoing_event)
        for mogi in all_mogis:
            if mogi.channel_id_in_rooms(interaction.channel.id):
                room = mogi.get_room_from_channel_id(interaction.channel.id)
                bottom_room_num = len(mogi.rooms)
                return room, bottom_room_num
        return None, 1

    @app_commands.command(name="sub")
    @app_commands.guild_only()
    # @commands.cooldown(rate=1, per=120, type=commands.BucketType.user)
    async def sub(self, interaction: discord.Interaction, races_left: app_commands.Range[int, 1, 12]):
        """Sends out a request for a sub in the sub channel. Only works in thread channels for SQ rooms."""
        current_time = time.time()
        lastCommandTime = cooldowns.get(interaction.user.id)
        print(
            f"{interaction.user.name} requests a sub, previous sub command time: {lastCommandTime}",
            flush=True)
        if lastCommandTime is None:
            lastCommandTime = 0

        # Cooldown timer in seconds
        if (current_time - lastCommandTime) < 120 and not self.is_staff(interaction.user):
            await interaction.response.send_message(
                f"You are still on cooldown. Please wait for {int(2 * 60 - (current_time - lastCommandTime))} more seconds to use this command again.",
                ephemeral=True)
            return

        room, bottom_room_num = self.find_room_by_interaction(interaction)
        if room is None:
            await interaction.response.send_message(
                f"More than {self.MOGI_LIFETIME} minutes have passed since mogi start, the Mogi Object has been deleted.",
                ephemeral=True)
            return
        frequently_tagged_role_id = self.bot.config["frequently_tagged_role_id"]
        msg = f"<@&{frequently_tagged_role_id}> - "
        if bottom_room_num == 1:
            msg += f"Room 1 is looking for a sub for {races_left} more races with any mmr\n"
        elif room.room_num == 1:
            msg += f"Room 1 is looking for a sub for {races_left} more races with mmr >{room.mmr_low - self.SUB_RANGE_MMR_ALLOWANCE}\n"
        elif room.room_num == bottom_room_num:
            msg += f"Room {room.room_num} is looking for a sub for {races_left} more races with mmr <{room.mmr_high + self.SUB_RANGE_MMR_ALLOWANCE}\n"
        else:
            msg += f"Room {room.room_num} is looking for a sub for {races_left} more races with range {room.mmr_low - self.SUB_RANGE_MMR_ALLOWANCE}-{room.mmr_high + self.SUB_RANGE_MMR_ALLOWANCE}\n"
        message_delete_date = datetime.now(
            timezone.utc) + timedelta(seconds=self.SUB_MESSAGE_LIFETIME_SECONDS)
        msg += f"Message will auto-delete in {discord.utils.format_dt(message_delete_date, style='R')}"
        await self.SUB_CHANNEL.send(msg, delete_after=self.SUB_MESSAGE_LIFETIME_SECONDS)
        view = JoinView(room,
                        self.ratings.get_rating_from_discord_id,
                        self.SUB_RANGE_MMR_ALLOWANCE,
                        bottom_room_num,
                        is_restricted)
        await self.SUB_CHANNEL.send(view=view, delete_after=self.SUB_MESSAGE_LIFETIME_SECONDS)
        cooldowns[interaction.user.id] = current_time  # Updates cooldown
        await interaction.response.send_message("Sent out request for sub.")

    @tasks.loop(seconds=30)
    async def list_task(self):
        """Continually display the list of confirmed players for a mogi in the list channel"""
        mogi = self.ongoing_event
        if mogi is not None:
            if not mogi.gathering:
                await self.delete_list_messages(0)
                return
            all_confirmed_players = mogi.players_on_confirmed_teams()
            first_late_player_index = (
                mogi.num_players // mogi.players_per_room) * mogi.players_per_room
            on_time_players = sorted(
                all_confirmed_players[:first_late_player_index], reverse=True)
            late_players = all_confirmed_players[first_late_player_index:]

            msg = f"**Queue closing: {discord.utils.format_dt(mogi.start_time)}**\n\n"
            format_str = f"{mogi.format} " if mogi.format else ""
            msg += f"**Current {format_str}Mogi List:**\n"
            if common.SERVER is common.Server.MKW:
                for i, player in enumerate(on_time_players, 1):
                    adjusted_mmr_text = f"MMR -> {player.adjusted_mmr} " if player.is_matchmaking_mmr_adjusted else ""
                    msg += f"`{i}.` {player.lounge_name} ({player.mmr} {adjusted_mmr_text}MMR)\n"
                    if i % mogi.players_per_room == 0:
                        msg += "ㅤ\n"
                if len(on_time_players) == 0:  # Text looks better this way
                    msg += "\n"
                msg += "**Late Players:**\n"
                for i, player in enumerate(late_players, 1):
                    adjusted_mmr_text = f"MMR -> {player.adjusted_mmr} " if player.is_matchmaking_mmr_adjusted else ""
                    msg += f"`{i}.` {player.lounge_name} ({player.mmr} {adjusted_mmr_text}MMR)\n"
            elif common.SERVER is common.Server.MK8DX:
                all_confirmed_players.sort(reverse=True)
                for i, player in enumerate(all_confirmed_players, 1):
                    late_str = " `*`" if player in late_players else ""
                    msg += f"`{i}.` {player.lounge_name} ({player.mmr} MMR){late_str}\n"
                    if i % mogi.players_per_room == 0:
                        msg += "ㅤ\n"
            msg += f"\n**Last Updated:** {discord.utils.format_dt(datetime.now(timezone.utc), style='R')}"
            message = msg.split("\n")

            new_messages = []
            bulk_msg = ""
            for i in range(len(message)):
                if len(bulk_msg + message[i] + "\n") > 2000:
                    new_messages.append(bulk_msg)
                    bulk_msg = ""
                bulk_msg += message[i] + "\n"
            if len(bulk_msg) > 0 and bulk_msg != "\n":
                new_messages.append(bulk_msg)

            await self.delete_list_messages(len(new_messages))

            try:
                for i, message in enumerate(new_messages):
                    if i < len(self.list_messages):
                        old_message = self.list_messages[i]
                        await old_message.edit(content=message)
                    else:
                        new_message = await self.LIST_CHANNEL.send(message)
                        self.list_messages.append(new_message)
            except BaseException:
                await self.delete_list_messages(0)
                for i, message in enumerate(new_messages):
                    new_message = await self.LIST_CHANNEL.send(message)
                    self.list_messages.append(new_message)
        else:
            await self.delete_list_messages(0)

    async def delete_list_messages(self, new_list_size: int):
        try:
            messages_to_delete = []
            while len(self.list_messages) > new_list_size:
                messages_to_delete.append(self.list_messages.pop())
            if self.LIST_CHANNEL and len(messages_to_delete) > 0:
                await self.LIST_CHANNEL.delete_messages(messages_to_delete)
        except Exception as e:
            print(traceback.format_exc())

    async def update_forced_format_list(self):
        """Update the list of Mogis with scheduled formats"""

        if self.SCHEDULE_CHANNEL:
            msg = ""

            if common.SERVER is common.Server.MKW:
                msg += "**Daily Queue Times:**\n\n"

                curr_time = self.compute_next_event_open_time() + self.JOINING_TIME + \
                    self.DISPLAY_OFFSET_MINUTES
                cutoff_time = curr_time + timedelta(hours=24)

                while curr_time < cutoff_time:
                    msg += f"{discord.utils.format_dt(curr_time, style='t')}\n"
                    curr_time += self.QUEUE_OPEN_TIME

                msg += "\n"

            msg += "**List of Mogis with Scheduled Formats:**\n\n"
            for index, event in enumerate(self.forced_format_times):
                msg += f"{index + 1}) {discord.utils.format_dt(event[0])} - {event[1]}\n"
            message = msg.split("\n")

            new_messages = []
            bulk_msg = ""
            for i in range(len(message)):
                if len(bulk_msg + message[i] + "\n") > 2000:
                    new_messages.append(bulk_msg)
                    bulk_msg = ""
                bulk_msg += message[i] + "\n"
            if len(bulk_msg) > 0 and bulk_msg != "\n":
                new_messages.append(bulk_msg)

            await self.delete_format_messages(len(new_messages))

            try:
                for i, message in enumerate(new_messages):
                    if i < len(self.format_messages):
                        old_message = self.format_messages[i]
                        await old_message.edit(content=message)
                    else:
                        new_message = await self.SCHEDULE_CHANNEL.send(message)
                        self.format_messages.append(new_message)
            except BaseException:
                await self.delete_format_messages(0)
                for i, message in enumerate(new_messages):
                    new_message = await self.SCHEDULE_CHANNEL.send(message)
                    self.format_messages.append(new_message)

    async def delete_format_messages(self, new_list_size: int):
        try:
            messages_to_delete = []
            while len(self.format_messages) > new_list_size:
                messages_to_delete.append(self.format_messages.pop())
            if self.SCHEDULE_CHANNEL and len(messages_to_delete) > 0:
                await self.SCHEDULE_CHANNEL.delete_messages(messages_to_delete)
        except Exception as e:
            print(traceback.format_exc())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not (
            message.content.isdecimal() and 12 <= int(
                message.content) <= 180):
            return
        mogi = None
        if self.ongoing_event is not None:
            mogi = self.ongoing_event if self.ongoing_event.channel_id_in_rooms(
                message.channel.id) else None
        if mogi is None:
            mogi = discord.utils.find(lambda m: m.channel_id_in_rooms(
                message.channel.id), self.old_events)
        if mogi is None:
            return
        room = discord.utils.find(
            lambda r: r.channel.id == message.channel.id, mogi.rooms)
        if room is None or not room.teams:
            return
        team = discord.utils.find(
            lambda t: t.has_player(message.author), room.teams)
        if team is None:
            return
        player = discord.utils.find(
            lambda p: p.member.id == message.author.id, team.players)
        if player is not None:
            player.score = int(message.content)

    @app_commands.command(name="scoreboard")
    @app_commands.guild_only()
    async def scoreboard(self, interaction: discord.Interaction):
        """Displays the scoreboard of the room. Only works in thread channels for SQ rooms."""
        if common.SERVER is not common.Server.MK8DX:
            await interaction.response.send_message(f"Command is only usable for MK8DX.", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(f"Cannot use this command here.", ephemeral=True)
            return

        mogi = discord.utils.find(lambda mogi: mogi.channel_id_in_rooms(
            interaction.channel_id), self.old_events)
        if not mogi:
            await interaction.response.send_message(f"The Mogi object cannot be found.", ephemeral=True)
            return

        room = discord.utils.find(
            lambda room: room.channel.id == interaction.channel_id, mogi.rooms)
        if not room:
            await interaction.response.send_message(f"The Thread object cannot be found.", ephemeral=True)
            return

        format_ = round(12 / len(room.teams))

        msg = f"!submit {format_} {room.tier}\n"
        for team in room.teams:
            for player in team.players:
                msg += f"{player.lounge_name} {player.score}\n"
            if format_ != 1:
                msg += "\n"
        await interaction.response.send_message(msg)

    @app_commands.command(name="remove_player")
    @app_commands.guild_only()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        """Removes a specific player from the current queue.  Staff use only."""
        mogi = self.get_mogi(interaction)
        if mogi is None or not mogi.started or not mogi.gathering:
            await interaction.response.send_message("Queue has not started yet.")
            return

        squad = mogi.check_player(member)
        if squad is None:
            await interaction.response.send_message(f"{member.display_name} is not currently in this event")
            return
        mogi.teams.remove(squad)
        msg = "Staff has removed "
        msg += ", ".join([p.lounge_name for p in squad.players])
        msg += f" from the mogi {discord.utils.format_dt(mogi.start_time, style='R')}"
        msg += f", `[{mogi.count_registered()} players]`"

        event_status_launched = self.check_close_event_change()
        try:
            await interaction.response.send_message(msg)
        finally:
            if event_status_launched:
                await self.launch_mogi()

    @app_commands.command(name="ping_staff")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.user.id)
    async def ping_staff(self, interaction: discord.Interaction, role: str):
        """Pings the specified staff role for help."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(f"Cannot use this command here.", ephemeral=True)
            return
        if is_restricted(interaction.user):
            await interaction.response.send_message(f"You are restricted from using this command.", ephemeral=True)
            return
        if role not in self.helper_staff_roles:
            await interaction.response.send_message(f"You are not allowed to ping this role for help. Valid roles to ping: `{', '.join(self.helper_staff_roles)}`", ephemeral=True)
            return
        await interaction.response.send_message(f"{self.helper_staff_roles[role].mention}, {interaction.user.mention} is requesting help.", allowed_mentions=discord.AllowedMentions(roles=True))

    @ping_staff.autocomplete('role')
    async def ping_staff_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=role_name, value=role_name)
            for role_name in self.helper_staff_roles if current.lower() in role_name.lower()
        ]

    def allow_new_mogis(self):
        self.LAUNCH_NEW_EVENTS = True

    @app_commands.command(name="annul_current_mogi")
    @app_commands.guild_only()
    async def annul_current_mogi(self, interaction: discord.Interaction, resume_mogis_after: bool):
        """The mogi currently gathering will be deleted.  resume_mogis_after determines whether future mogis will be scheduled.  Staff use only."""
        self.next_event = None
        self.ongoing_event = None
        self.LAUNCH_NEW_EVENTS = False
        msg = "The current mogi has been cancelled, "
        if resume_mogis_after:
            next_event_open_time = self.compute_next_event_open_time()
            next_event_start_time = (
                next_event_open_time + self.QUEUE_OPEN_TIME)
            delay = (next_event_start_time -
                     datetime.now(timezone.utc)).total_seconds()
            asyncio.get_event_loop().call_later(delay, self.allow_new_mogis)
            msg += f"the queue will resume at {next_event_start_time}."
        else:
            msg += "future mogis will not be started."
        await self.lockdown(self.MOGI_CHANNEL)
        await interaction.response.send_message(msg)

    @app_commands.command(name="pause_mogi_scheduling")
    @app_commands.guild_only()
    async def pause_mogi_scheduling(self, interaction: discord.Interaction):
        """The mogi that is currently gathering will continue to work.  Future mogis cannot be scheduled.  Staff use only."""
        self.LAUNCH_NEW_EVENTS = False
        await interaction.response.send_message("Future Mogis will not be started.")

    @app_commands.command(name="resume_mogi_scheduling")
    @app_commands.guild_only()
    async def resume_mogi_scheduling(self, interaction: discord.Interaction):
        """Mogis will begin to be scheduled again.  Staff use only."""
        self.LAUNCH_NEW_EVENTS = True
        await interaction.response.send_message("Mogis will resume scheduling.")

    @app_commands.command(name="peek_bot_config")
    @app_commands.guild_only()
    async def peek_bot_config(self, interaction: discord.Interaction):
        """View the configured values for the Queue System.  Staff use only."""
        msg = ""
        msg += f"LOUNGE TYPE: {self.bot.config['lounge']}\n"
        if common.SERVER is common.Server.MKW:
            msg += f"TRACK TYPE: {self.TRACK_TYPE}\n"
        msg += f"CURRENT SERVER: {self.GUILD}\n"
        msg += f"GENERAL CHANNEL: {self.GENERAL_CHANNEL}\n"
        msg += f"LIST CHANNEL: {self.LIST_CHANNEL}\n"
        msg += f"JOIN CHANNEL: {self.MOGI_CHANNEL}\n"
        msg += f"SUB CHANNEL: {self.SUB_CHANNEL}\n"
        msg += f"HISTORY CHANNEL: {self.HISTORY_CHANNEL}\n"

        admins = []
        for m in self.bot.config["admin_roles"]:
            role = self.GUILD.get_role(m)
            if role:
                admins.append(role.name)
        msg += f"ADMIN ROLES: {', '.join(admins)}\n"

        helper_staff = []
        for m in self.bot.config["helper_staff_roles"]:
            role = self.GUILD.get_role(m)
            if role:
                helper_staff.append(role.name)
        msg += f"HELPER STAFF ROLES: {', '.join(helper_staff)}\n"

        members_for_channels = []
        for m in self.bot.config["members_for_channels"]:
            member = self.GUILD.get_member(m)
            if member:
                members_for_channels.append(member.name)
        msg += f"MEMBERS ADDED TO EACH ROOM: {', '.join(members_for_channels)}\n"

        roles_for_channels = []
        for m in self.bot.config["roles_for_channels"]:
            role = self.GUILD.get_role(m)
            if role:
                roles_for_channels.append(role.name)
        msg += f"ROLES ADDED TO EACH ROOM: {', '.join(roles_for_channels)}\n"

        msg += f"PLACEMENT PLAYER STARTING MMR: {self.PLACEMENT_PLAYER_MMR}\n"
        msg += f"SITE URL: {self.URL}\n"
        msg += f"FIRST EVENT TIME: {self.FIRST_EVENT_TIME}\n"
        msg += f"TIME BETWEEN EVENTS: {self.QUEUE_OPEN_TIME} minutes\n"
        msg += f"DISPLAY TIME OFFSET MINUTES FROM JOIN TIME END: {self.DISPLAY_OFFSET_MINUTES} minutes\n"
        msg += f"EVENT JOINING TIME: {self.JOINING_TIME} minutes\n"
        msg += f"EXTENSION TIME: {self.EXTENSION_TIME} minutes\n"
        msg += f"ROOM JOIN PENALTY TIME: {self.ROOM_JOIN_PENALTY_TIME} minutes\n"
        msg += f"EVENT LIFETIME: {self.MOGI_LIFETIME} minutes\n"
        msg += f"EXTRA SUB RANGE ALLOWANCE: +- {self.SUB_RANGE_MMR_ALLOWANCE} MMR\n"
        msg += f"SUB MESSAGE LIFETIME: {self.SUB_MESSAGE_LIFETIME_SECONDS} seconds\n"
        msg += f"ROOM MMR THRESHOLD: {self.room_mmr_threshold}\n"
        msg += f"MATCHMAKING BOTTOM MMR: {common.CONFIG['MATCHMAKING_BOTTOM_MMR']}\n"
        msg += f"MATCHMAKING TOP MMR: {common.CONFIG['MATCHMAKING_TOP_MMR']}"
        await interaction.response.send_message(msg)

    @app_commands.command(name="reset_bot")
    @app_commands.guild_only()
    async def reset_bot(self, interaction: discord.Interaction):
        """Resets the bot.  Staff use only."""
        self.next_event = None
        self.ongoing_event = None
        self.old_events = []
        self.LAUNCH_NEW_EVENTS = True
        await interaction.response.send_message("All events have been deleted.  Queue will restart shortly.")

    async def timezone_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        current_upper = current.upper()
        now = datetime.now(pytz.utc)
        active_timezones = []

        for tz, info in self.timezones.items():
            full_timezone_name = info['zone_name']
            tz_info = pytz.timezone(full_timezone_name)
            local_now = now.astimezone(tz_info)

            if local_now.utcoffset().total_seconds() / 3600 == info['offset']:
                if current_upper in tz.upper():
                    active_timezones.append((tz, info['offset']))

        active_timezones.sort(key=lambda x: (x[1], x[0]))

        choices = [
            app_commands.Choice(name=f"{tz} (UTC{offset:+})", value=tz)
            for tz, offset in active_timezones
        ]

        return choices[:25]

    async def date_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        timezone_choice = pytz.timezone("Etc/UTC")

        if 'options' in interaction.data:
            for option in interaction.data['options']:
                if option['name'] == 'tzone':
                    abbreviation = option['value']
                    timezone_info = self.timezones.get(abbreviation, {})
                    full_timezone_name = timezone_info.get(
                        "zone_name", "Etc/UTC")
                    timezone_choice = pytz.timezone(full_timezone_name)
                    break

        today = datetime.now(timezone_choice) + self.QUEUE_OPEN_TIME
        today_date = today.date()
        options = []
        for i in range(25):
            date = today_date + timedelta(days=i)
            name = date.strftime("%m/%d")
            value = date.isoformat()
            options.append(app_commands.Choice(name=name, value=value))

        return options

    def compute_first_event_of_date_open_time(self, date):
        time_elapsed = date - self.FIRST_EVENT_TIME
        num_launched_events = time_elapsed // self.QUEUE_OPEN_TIME
        next_event_open_time = self.FIRST_EVENT_TIME + \
            (self.QUEUE_OPEN_TIME * num_launched_events)
        return next_event_open_time

    async def time_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        date = None
        timezone_choice = pytz.timezone("Etc/UTC")
        timezone_offset = 0

        if 'options' in interaction.data:
            for option in interaction.data['options']:
                if option['name'] == 'date':
                    date = option['value']
                if option['name'] == 'tzone':
                    abbreviation = option['value']
                    timezone_info = self.timezones.get(abbreviation, {})
                    full_timezone_name = timezone_info.get(
                        "zone_name", "Etc/UTC")
                    timezone_offset = timezone_info.get(
                        "offset", 0)
                    timezone_choice = pytz.timezone(full_timezone_name)

        time_options = []
        if date and timezone_choice:
            try:
                selected_date = datetime.fromisoformat(date)
                selected_date = selected_date.astimezone(
                    timezone.utc) - timedelta(hours=timezone_offset)

                curr_time = datetime.now(
                    timezone.utc)
                curr_date = self.compute_first_event_of_date_open_time(
                    selected_date)
                cutoff_date = selected_date + timedelta(hours=24)

                while curr_date < cutoff_date and len(time_options) < 25:
                    if curr_date > curr_time:
                        adjusted_date = curr_date + self.JOINING_TIME + self.DISPLAY_OFFSET_MINUTES
                        tz_adjusted_date = adjusted_date + \
                            timedelta(hours=timezone_offset)
                        dt_str = adjusted_date.isoformat()
                        name = tz_adjusted_date.strftime(
                            f"%m/%d - %H:%M:%S")
                        time_options.append(
                            app_commands.Choice(name=name, value=dt_str))
                    curr_date += self.QUEUE_OPEN_TIME

            except ValueError as e:
                print(f"Error parsing date: {e}", flush=True)

        else:
            time_options = [f"{hour:02d}:00" for hour in range(24)]
            return [app_commands.Choice(name=time, value=time) for time in time_options]

        return time_options

    async def format_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        options = ["FFA", "2v2", "3v3", "4v4", "6v6"]
        return [app_commands.Choice(name=option, value=option) for option in options]

    @app_commands.command(name="schedule_forced_format_times")
    @app_commands.autocomplete(tzone=timezone_autocomplete)
    @app_commands.autocomplete(date=date_autocomplete)
    @app_commands.autocomplete(time=time_autocomplete)
    @app_commands.autocomplete(format=format_autocomplete)
    @app_commands.guild_only()
    async def schedule_forced_format_times(self, interaction: discord.Interaction, tzone: str, date: str, time: str, format: str):
        """Schedule mogis to have all rooms be specific format. Staff use only."""
        curr_time = datetime.now(timezone.utc)
        dt = datetime.fromisoformat(time).replace(tzinfo=timezone.utc)
        timestamp = dt.timestamp()

        if curr_time > dt:
            msg = ""
            msg += f"Timestamp {timestamp} represents {time} and is in the past, submit a future date.\n"
            msg += "This timestamp has not been added."
            await interaction.response.send_message(msg)
            return

        new_event = (dt, format)
        event_dict = {event[0]: event for event in self.forced_format_times}
        event_dict[new_event[0]] = new_event
        updated_list = list(event_dict.values())
        self.forced_format_times = sorted(updated_list, key=lambda x: x[0])

        if self.SCHEDULE_CHANNEL:
            await self.update_forced_format_list()

        msg = f"Added new {format} mogi at {discord.utils.format_dt(dt)}"

        await interaction.response.send_message(msg)

    async def autoschedule_forced_format_times(self):
        """Schedule the next batch of forced format mogis automatically."""
        event_dict = {event[0]: event for event in self.forced_format_times}
        time_between_ff_queues = self.queues_between_forced_format_queue * self.QUEUE_OPEN_TIME

        last_event = self.FORCED_FORMAT_FIRST_EVENT
        start_index = 0
        while last_event < datetime.now(timezone.utc):
            last_event += time_between_ff_queues
            start_index += 1

        total_iterations = self.FORCED_FORMAT_AUTOSCHEDULE_AMOUNT * \
            len(self.forced_format_order)

        for i in range(total_iterations):
            current_index = (
                start_index + i + self.FORCED_FORMAT_ORDER_OFFSET) % len(self.forced_format_order)
            format = self.forced_format_order[current_index]

            new_event_time = last_event + i * time_between_ff_queues + \
                self.JOINING_TIME + self.DISPLAY_OFFSET_MINUTES
            new_event = (new_event_time, format)
            event_dict[new_event[0]] = new_event

        updated_list = list(event_dict.values())
        self.forced_format_times = sorted(updated_list, key=lambda x: x[0])

        if self.SCHEDULE_CHANNEL:
            await self.update_forced_format_list()

    async def remove_time_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = [
            app_commands.Choice(
                name=f"{dt.strftime('%Y-%m-%d %H:%M:%S')} - {fmt}", value=dt.isoformat()
            )
            for dt, fmt in self.forced_format_times[:25]
        ]
        return choices

    @app_commands.command(name="remove_forced_format_time")
    @app_commands.autocomplete(time=remove_time_autocomplete)
    async def remove_forced_format_time(self, interaction: discord.Interaction, time: str):
        """Remove a specific forced format time by datetime.  Staff use only."""
        try:
            dt_to_remove = datetime.fromisoformat(time)

            original_count = len(self.forced_format_times)
            self.forced_format_times = [
                entry for entry in self.forced_format_times if entry[0] != dt_to_remove
            ]

            if len(self.forced_format_times) < original_count:
                await interaction.response.send_message(f"Removed {discord.utils.format_dt(dt_to_remove, 'f')} from the Forced Format times.")
            else:
                await interaction.response.send_message(f"No entry found for {time}.")

            if self.SCHEDULE_CHANNEL:
                await self.update_forced_format_list()
        except ValueError:
            await interaction.response.send_message("Invalid datetime format provided.")

    @app_commands.command(name="clear_forced_format_times")
    @app_commands.guild_only()
    async def clear_forced_format_times(self, interaction: discord.Interaction):
        """Clears current list of forced format times.  Staff use only."""
        self.forced_format_times = []
        if self.SCHEDULE_CHANNEL:
            await self.update_forced_format_list()

        await interaction.response.send_message("Cleared list of Forced Format Times.")

    @app_commands.command(name="schedule_sq_times")
    @app_commands.autocomplete(tzone=timezone_autocomplete)
    @app_commands.autocomplete(date=date_autocomplete)
    @app_commands.autocomplete(time=time_autocomplete)
    @app_commands.guild_only()
    async def schedule_sq_times(self, interaction: discord.Interaction, tzone: str, date: str, time: str):
        """Schedule Squad Queue times. Staff use only."""
        curr_time = datetime.now(timezone.utc)
        dt = datetime.fromisoformat(time).replace(tzinfo=timezone.utc)
        timestamp = dt.timestamp()

        if curr_time > dt:
            msg = ""
            msg += f"Timestamp {timestamp} represents {time} and is in the past, submit a future date.\n"
            msg += "This timestamp has not been added."
            await interaction.response.send_message(msg)
            return

        self.sq_times.append(dt)
        self.sq_times = list(set(self.sq_times))
        list.sort(self.sq_times)

        msg = "List of Squad Queue Times:\n"

        for index, date in enumerate(self.sq_times):
            msg += f"{index + 1}) {discord.utils.format_dt(date)}\n"

        await interaction.response.send_message(msg)

    @app_commands.command(name="peek_sq_times")
    @app_commands.guild_only()
    async def peek_sq_times(self, interaction: discord.Interaction):
        """Peeks the current list of sq times.  Staff use only."""
        msg = "List of Squad Queue Times:\n"

        for index, date in enumerate(self.sq_times):
            msg += f"{index + 1}) {date}\n"

        await interaction.response.send_message(msg)

    async def sq_time_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = [
            app_commands.Choice(name=dt.strftime(
                "%Y-%m-%d %H:%M:%S"), value=dt.isoformat())
            for dt in self.sq_times[:25]
        ]
        return choices

    @app_commands.command(name="remove_sq_time")
    @app_commands.autocomplete(time=sq_time_autocomplete)
    async def remove_sq_time(self, interaction: discord.Interaction, time: str):
        """Remove a specific Squad Queue time.  Staff use only."""
        dt_to_remove = datetime.fromisoformat(time)

        if dt_to_remove in self.sq_times:
            self.sq_times.remove(dt_to_remove)
            await interaction.response.send_message(f"Removed {discord.utils.format_dt(dt_to_remove, 'f')} from the Squad Queue times.")
        else:
            await interaction.response.send_message("Specified time not found in the Squad Queue times.", ephemeral=True)

    @app_commands.command(name="clear_sq_times")
    @app_commands.guild_only()
    async def clear_sq_times(self, interaction: discord.Interaction):
        """Clears current list of sq times.  Staff use only."""
        self.sq_times = []

        await interaction.response.send_message("Cleared list of Squad Queue Times.")

    @app_commands.command(name="update_tier_info")
    @app_commands.guild_only()
    async def update_tier_info(self, interaction: discord.Interaction):
        """Updates the mmr divisions that denote each tier.  Staff use only."""
        msg = await self.get_ladder_info()
        await interaction.response.send_message(msg)

    async def get_ladder_info(self):
        timeout = aiohttp.ClientTimeout(total=10)
        url = "https://mkwlounge.gg/api/ladderclass.php?ladder_type=" + self.TRACK_TYPE
        msg = ""
        try:
            async with aiohttp.ClientSession(
                    timeout=timeout,
                    auth=aiohttp.BasicAuth(
                        "username", "password")) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        raise Exception(
                            "Fetch for tier info has failed, bad status code")
                    result = await resp.json()
                    if result['status'] != "success":
                        raise Exception(
                            "Fetch for tier info has failed, Status: Failure")
                    self.TIER_INFO = result["results"]
                    msg += "Fetch for Tier Info Successful.\n"
                    for tier in self.TIER_INFO:
                        boundary = ""
                        if tier["minimum_mmr"]:
                            boundary += f"{tier['minimum_mmr']}-"
                        else:
                            boundary += "<"
                        if tier["maximum_mmr"]:
                            boundary += f"{tier['maximum_mmr']}"
                        else:
                            boundary = f">{tier['minimum_mmr']}"
                        msg += f"T{tier['ladder_order']} Boundary: {boundary}\n"
                    print(msg, flush=True)
                    return msg
        except Exception as e:
            print(traceback.format_exc())

    @staticmethod
    async def end_voting(mogi: Mogi):
        """Ends voting in all rooms with ongoing votes."""
        try:
            if mogi and not mogi.format:
                for index, room in enumerate(mogi.rooms, 1):
                    if not room or not room.view:
                        print(
                            f"Skipping room {index} in function end_voting.",
                            flush=True)
                        continue
                    await room.view.find_winner()

        except Exception as e:
            print(traceback.format_exc())

    @staticmethod
    async def write_history(mogi: Mogi, history_channel: discord.TextChannel):
        """Writes the teams, tier and average of each room per hour."""
        try:
            if mogi is not None:
                format_str = f"{mogi.format} " if mogi.format else ""
                await history_channel.send(f"{discord.utils.format_dt(mogi.display_time)} {format_str}Rooms")
                for index, room in enumerate(mogi.rooms, 1):
                    if not room or not room.view:
                        print(
                            f"Skipping room {index} in function write_history.",
                            flush=True)
                        continue
                    msg = room.view.header_text
                    if common.SERVER is common.Server.MKW:
                        msg += f"{room.view.room_start_msg_link}\n"
                    elif common.SERVER is common.Server.MK8DX:
                        msg += f"{room.channel.jump_url}\n"
                    msg += room.view.teams_text
                    msg += "ㅤ"
                    try:
                        await history_channel.send(msg)
                    except discord.HTTPException as e:
                        print(
                            f"HTTP error while writing room {index} in function write_history, skipping to next room.",
                            flush=True)
        except Exception as e:
            print(traceback.format_exc())

    # make thread channels while the event is gathering instead of at the end,
    # since discord only allows 50 thread channels to be created per 5 minutes.
    async def check_room_threads(self, mogi: Mogi):
        num_created_rooms = len(mogi.rooms)
        for i in range(num_created_rooms, mogi.max_possible_rooms):
            if not common.CONFIG["USE_THREADS"]:
                mogi.rooms.append(Room(None, i + 1, None, self.TIER_INFO))
                continue
            display_time = mogi.display_time
            minute = display_time.minute
            if len(str(minute)) == 1:
                minute = '0' + str(minute)
            room_name = f"{display_time.month}/{display_time.day}, {display_time.hour}:{minute}:00 - Room {i+1}"
            try:
                room_channel = await self.GENERAL_CHANNEL.create_thread(name=room_name,
                                                                        auto_archive_duration=60,
                                                                        invitable=False)
                # Address race condition - race condition would result in
                # making too many rooms
                if len(mogi.rooms) >= mogi.max_possible_rooms:
                    await room_channel.delete()
                    return

            except Exception as e:
                print(traceback.format_exc())
                err_msg = f"\nAn error has occurred while creating a room channel:\n{e}"
                await mogi.mogi_channel.send(err_msg)
                return
            mogi.rooms.append(Room(None, i + 1, room_channel, self.TIER_INFO))

    @staticmethod
    async def handle_voting_and_history(mogi: Mogi, history_channel: discord.TextChannel):
        # We could have used asyncio.call_later(120, handle_voting_and_history)
        # in the caller's code
        if not mogi.format:
            await asyncio.sleep(120)
        await SquadQueue.end_voting(mogi)
        await SquadQueue.write_history(mogi, history_channel)

    # add teams to the room threads that we have already created
    async def add_teams_to_rooms(self, mogi: Mogi, open_time: int, started_automatically=False):
        if open_time >= 60 or open_time < 0:
            await mogi.mogi_channel.send("Please specify a valid time (in minutes) for rooms to open (00-59)")
            return
        if mogi.making_rooms_run and started_automatically:
            return

        await self.lockdown(mogi.mogi_channel)
        if mogi.max_possible_rooms == 0:
            self.ongoing_event = None
            await mogi.mogi_channel.send(f"Not enough players to fill a single room! This mogi will be cancelled.")
            return

        was_gathering = mogi.gathering
        mogi.making_rooms_run = True
        mogi.gathering = False

        await self.check_room_threads(mogi)

        if was_gathering:
            await mogi.mogi_channel.send("Mogi is now closed; players can no longer join or drop from the event")

        extra_members = []

        for m in self.bot.config["members_for_channels"]:
            extra_members.append(mogi.mogi_channel.guild.get_member(m))
        for r in self.bot.config["roles_for_channels"]:
            extra_members.append(mogi.mogi_channel.guild.get_role(r))

        all_confirmed_players = mogi.players_on_confirmed_teams()
        first_late_player_index = (
            mogi.num_players // mogi.players_per_room) * mogi.players_per_room
        regular_player_list = all_confirmed_players[:first_late_player_index]
        late_player_list = all_confirmed_players[first_late_player_index:]
        proposed_list = mogi.generate_proposed_list(self.allowed_players_check)
        await mogi.populate_host_fcs()
        for room_number, room_players in enumerate(
                divide_chunks(proposed_list, mogi.players_per_room), 1):
            mogi_channel_msg = f"`Room {room_number} - Player List`\n"
            room_msg = mogi_channel_msg
            for player_num, player in enumerate(room_players, 1):
                added_str = ": **Added from late players**" if player in late_player_list else ""
                adjusted_mmr_text = f"MMR -> {player.adjusted_mmr} " if player.is_matchmaking_mmr_adjusted else ""
                mogi_channel_msg += f"`{player_num}.` {player.lounge_name} ({player.mmr} {adjusted_mmr_text}MMR){added_str}\n"
                room_msg += f"`{player_num}.` {player.lounge_name} ({player.mmr} MMR)\n"
            if not self.allowed_players_check(room_players):
                mogi_channel_msg += f"\nThe mmr gap in the room is higher than the allowed threshold of {self.room_mmr_threshold} MMR, this room has been cancelled."
            else:
                curr_room = mogi.rooms[room_number - 1]
                curr_room.teams = [Team([p]) for p in room_players]
                curr_room.create_host_list()
                player_mentions = " ".join([p.mention for p in room_players])
                extra_member_mentions = " ".join(
                    [m.mention for m in extra_members if m is not None])

                potential_host_str = curr_room.get_host_str()
                if potential_host_str == "":
                    potential_host_str += f"No one in the room queued as host. Decide a host amongst yourselves."
                room_msg += f"\n{potential_host_str}\n"

                if not mogi.format:
                    vote_formats = "FFA, 2v2, 3v3, 4v4" if common.SERVER is common.Server.MK8DX else "FFA, 2v2, 3v3, 4v4, 6v6"
                    room_msg += f"Vote for format {vote_formats}.\n"

                room_msg += f"""
{player_mentions} {extra_member_mentions}

If you need staff's assistance, use the `/ping_staff` command in this channel."""

                # The below try/except clause around Room.prepare_room_channel
                # only runs if the config's USE_THREADS is set to false
                try:
                    await curr_room.prepare_room_channel(self.GUILD, all_events=([self.ongoing_event] + self.old_events))
                # Non-fatal error, message already sent to room channel,
                # continue with room creation
                except mogi_objects.RoleAddFailure:
                    pass
                # semi-critical failures, but the room can proceed
                except mogi_objects.RoleNotFound as e:
                    await mogi.mogi_channel.send(f"**ERROR:** {e}")
                # critical failure, but we can recover by creating a thread
                except (mogi_objects.WrongChannelType, mogi_objects.NoFreeChannels) as e:
                    await mogi.mogi_channel.send(f"**ERROR:** {e}, attempting to create room thread instead...")
                    room_name = f"{mogi.display_time.month}/{mogi.display_time.day}, {mogi.display_time.hour}:{mogi.display_time.minute:02}:00 - Room {room_number}"
                    curr_room.channel = await self.GENERAL_CHANNEL.create_thread(name=room_name, auto_archive_duration=60, invitable=False)
                # critical failure, the room cannot proceed
                except mogi_objects.WrongChannelType as e:
                    await mogi.mogi_channel.send(f"**ERROR:** {e}")
                    continue

                try:
                    sent_msg = await curr_room.channel.send(room_msg)
                    view = VoteView(
                        room_players,
                        curr_room.channel,
                        mogi,
                        curr_room,
                        self.ROOM_JOIN_PENALTY_TIME,
                        sent_msg.jump_url)
                    curr_room.view = view
                    await view.create_message()
                except discord.DiscordException:
                    err_msg = f"\nAn error has occurred while creating the room channel; please contact your opponents in DM or another channel\n"
                    err_msg += player_mentions + extra_member_mentions
                    mogi_channel_msg += err_msg
                    print(traceback.format_exc())

            try:
                await mogi.mogi_channel.send(mogi_channel_msg)
            except discord.DiscordException:
                print(
                    f"Mogi Channel message for room {room_number} has failed to send.",
                    flush=True)
                print(traceback.format_exc())

        # Compute the list of "late" players that didn't get into any room
        not_in_proposed_list = [
            p for p in all_confirmed_players if p not in proposed_list]
        if len(not_in_proposed_list) > 0:
            mogi_channel_msg = "`Late players:`\n"
            for i, player in enumerate(not_in_proposed_list, 1):
                removed_str = ": **Removed from player list**" if player in regular_player_list else ""
                adjusted_mmr_text = f"MMR -> {player.adjusted_mmr} " if player.is_matchmaking_mmr_adjusted else ""
                mogi_channel_msg += f"`{i}.` {player.lounge_name} ({int(player.mmr)} {adjusted_mmr_text}MMR) {removed_str}\n"
            try:
                await mogi.mogi_channel.send(mogi_channel_msg)
            except discord.DiscordException:
                print("Late Player message has failed to send.", flush=True)
                print(traceback.format_exc())

        # We could have used asyncio.call_later(120, handle_voting_and_history)
        # and removed asyncio.sleep(120) in handle_voting_and_history
        if not common.CONFIG["USE_THREADS"]:
            asyncio.create_task(
                self.ongoing_event.assign_roles(guild=self.GUILD))
        asyncio.create_task(SquadQueue.handle_voting_and_history(
            self.ongoing_event, self.HISTORY_CHANNEL))
        if mogi.format:
            if self.SCHEDULE_CHANNEL:
                await self.update_forced_format_list()
        self.old_events.append(self.ongoing_event)
        self.ongoing_event = None

    def check_close_event_change(self) -> Tuple[bool, bool]:
        """Returns a bool indicating if the event was gathering but this function then closed the mogi depending on
        the time and number of players or other logic"""
        mogi = self.ongoing_event
        if mogi is not None:
            # If it's not automated, don't run this
            # If the mogi has not started, don't run this
            # If the mogi is not gathering, don't run this
            # If the mogi has already made the rooms, don't run this.
            # This logic was taken from a much more complex bot. It could be greatly simplified since all events here
            # are automated and follow a certain flow, but I am not going to
            # change what isn't broken.
            if not mogi.is_automated or not mogi.started or mogi.making_rooms_run or not mogi.gathering:
                return False
            cur_time = datetime.now(timezone.utc)
            force_start_time = mogi.start_time + \
                self.EXTENSION_TIME + mogi.additional_extension
            if force_start_time <= cur_time:
                mogi.gathering = False
                self.cur_extension_message = None
                return True
            num_leftover_teams = mogi.count_registered() % (12 // mogi.max_player_per_team)
            num_needed_teams = (
                12 // mogi.max_player_per_team) - num_leftover_teams
            if common.SERVER is common.Server.MKW and (
                force_start_time -
                timedelta(
                    minutes=1)) <= cur_time and num_leftover_teams != 0:
                if not mogi.has_checked_auto_extend:
                    mogi.has_checked_auto_extend = True
                    if (mogi.max_possible_rooms == 0 and num_needed_teams <= 2) or (
                            num_needed_teams - 1) <= mogi.max_possible_rooms:
                        self.cur_extension_message = f"The extension criteria has been met, so queueing has been extended for 2 more minutes."
                        mogi.additional_extension += timedelta(minutes=2)
                    return False
            if mogi.start_time <= cur_time and mogi.gathering:
                # check if there are an even amount of teams since we are past the queue time
                # If have en even number of players...
                if num_leftover_teams == 0:
                    # ...and none of the rooms will be cancelled, close the queue and begin
                    if not mogi.any_room_cancelled(self.allowed_players_check):
                        mogi.gathering = False
                        self.cur_extension_message = None
                        return True
                    # ...any of the rooms will be cancelled, set the error message
                    else:
                        self.last_extension_message_timestamp = datetime.now(
                            timezone.utc).replace(second=0, microsecond=0)
                        minutes_left = (force_start_time -
                                        cur_time).seconds // 60
                        self.cur_extension_message = f"One or more of the rooms will be cancelled, so the extension " \
                                                     f"will continue. Starting in {minutes_left + 1} minute(s) " \
                                                     f"regardless."

                else:
                    cur_extension_timestamp = datetime.now(
                        timezone.utc).replace(second=0, microsecond=0)
                    # At this point, we're in the extension time. So if the extension timestamp is in a different
                    # minute than the last one, we set the new extension
                    # message to be sent.
                    if cur_extension_timestamp != self.last_extension_message_timestamp:
                        self.last_extension_message_timestamp = cur_extension_timestamp
                        minutes_left = (force_start_time -
                                        cur_time).seconds // 60
                        self.cur_extension_message = f"Need {num_needed_teams} more player(s) to start immediately. Starting in {minutes_left + 1} minute(s) regardless."
        return False

    async def launch_mogi(self):
        mogi = self.ongoing_event
        if mogi is not None:
            await mogi.mogi_channel.send("Mogi is now closed; players can no longer join or drop from the event")
            await self.delete_list_messages(0)
            await self.add_teams_to_rooms(mogi, mogi.start_time.minute % 60, True)

    async def check_send_extension_message(self):
        if self.cur_extension_message is not None:
            to_send = self.cur_extension_message
            self.cur_extension_message = None
            if self.ongoing_event is not None:
                await self.ongoing_event.mogi_channel.send(to_send)

    async def scheduler_mogi_start(self):
        cur_time = datetime.now(timezone.utc)
        next_mogi = self.next_event
        if next_mogi is not None and (
                next_mogi.start_time -
                self.JOINING_TIME) < cur_time:
            # We are trying to launch the event - fail or not, we set next
            # event to None
            self.next_event = None
            # There is an ongoing event, but people are still queueing, so
            # remove it and fail
            if self.ongoing_event is not None and self.ongoing_event.gathering:
                await next_mogi.mogi_channel.send(
                    f"Because there is an ongoing event right now, the following event has been removed:\n{self.get_event_str(next_mogi)}\n")
                return
            # There is an ongoing event, but it has already started, so add it to the old events
            # This is potentially an issue - we should be checking if make
            # rooms has been run, not if it's started...?
            if self.ongoing_event is not None and self.ongoing_event.started:
                self.old_events.append(self.ongoing_event)

            # Put the next mogi as the current event and launch it
            self.ongoing_event = next_mogi
            self.ongoing_event.started = True
            self.ongoing_event.gathering = True
            await self.unlockdown(self.ongoing_event.mogi_channel)
            format_str = f"{self.ongoing_event.format} " if self.ongoing_event.format else ""
            await self.ongoing_event.mogi_channel.send(
                f"A queue is gathering for the {format_str}mogi {discord.utils.format_dt(self.ongoing_event.start_time, style='R')} - Type `/c` to join, `/ch` to join and volunteer to host, and `/d` to drop.")

    @tasks.loop(seconds=20.0)
    async def sqscheduler(self):
        """Scheduler that checks if it should start mogis and close them"""
        # It may seem silly to do try/except Exception, but this coroutine **cannot** fail
        # This coroutine *silently* fails and stops if exceptions aren't caught - an annoying abtraction of asyncio
        # This is unacceptable considering people are relying on these mogis to
        # run, so we will not allow this routine to stop
        try:
            if self.ongoing_event is None:
                await self.schedule_que_event()
        except Exception as e:
            print(traceback.format_exc())
        try:
            await self.scheduler_mogi_start()
        except Exception as e:
            print(traceback.format_exc())
        try:
            if self.check_close_event_change():
                await self.launch_mogi()
            await self.check_send_extension_message()
        except Exception as e:
            print(traceback.format_exc())

    def compute_next_event_open_time(self):
        cur_time = datetime.now(timezone.utc)
        time_elapsed = cur_time - self.FIRST_EVENT_TIME
        num_launched_events = time_elapsed // self.QUEUE_OPEN_TIME
        next_event_open_time = self.FIRST_EVENT_TIME + \
            (self.QUEUE_OPEN_TIME * num_launched_events)
        return next_event_open_time

    async def schedule_que_event(self):
        """Schedules next queue in the SquadQueue mogi queueing channel."""
        if self.GUILD is not None:
            if not self.LAUNCH_NEW_EVENTS:
                # (f"Not allowed to launch new events.", flush=True)
                return
            next_event_open_time = self.compute_next_event_open_time()
            next_event_start_time = next_event_open_time + self.JOINING_TIME
            next_event_display_time = next_event_start_time + self.DISPLAY_OFFSET_MINUTES
            # We don't want to schedule the next event if it would open after
            # it's joining period and during its extension period
            if next_event_start_time < datetime.now(timezone.utc):
                return
            format = None
            if len(
                    self.forced_format_times) > 0 and next_event_open_time + self.JOINING_TIME + self.DISPLAY_OFFSET_MINUTES == self.forced_format_times[0][0]:
                last_event = self.forced_format_times.pop(0)
                format = last_event[1]
                if len(self.forced_format_order) > 0 and self.queues_between_forced_format_queue and len(self.forced_format_times) == 0:
                    await self.autoschedule_forced_format_times()
            if len(
                    self.sq_times) > 0 and next_event_open_time + self.JOINING_TIME + self.DISPLAY_OFFSET_MINUTES == self.sq_times[0]:
                self.sq_times.pop(0)
                self.next_event = None
                self.ongoing_event = None
                self.LAUNCH_NEW_EVENTS = False
                next_event_start_time = next_event_open_time + self.QUEUE_OPEN_TIME
                delay = (next_event_start_time -
                         datetime.now(timezone.utc)).total_seconds()
                print(next_event_start_time, delay, flush=True)
                asyncio.get_event_loop().call_later(delay, self.allow_new_mogis)
                await self.lockdown(self.MOGI_CHANNEL)
                await self.MOGI_CHANNEL.send(
                    "Squad Queue is currently going on!  The queue will remain closed.")
                return
            self.next_event = Mogi(sq_id=1,
                                   max_players_per_team=1,
                                   players_per_room=12,
                                   mogi_channel=self.MOGI_CHANNEL,
                                   is_automated=True,
                                   start_time=next_event_start_time,
                                   display_time=next_event_display_time,
                                   format=format)

            print(f"Started Queue for {next_event_start_time}", flush=True)

    @tasks.loop(minutes=1)
    async def delete_old_mogis(self):
        """Deletes old mogi objects"""
        try:
            curr_time = datetime.now(timezone.utc)
            mogi_lifetime = timedelta(minutes=self.MOGI_LIFETIME)
            delete_queue = [m for m in self.old_events if (
                curr_time - mogi_lifetime) > m.start_time]
            for mogi in delete_queue:
                print(
                    f"Deleting {mogi.start_time} Mogi at {curr_time}",
                    flush=True)
                self.old_events.remove(mogi)
        except Exception as e:
            print(traceback.format_exc())

    @tasks.loop(minutes=10)
    async def maintain_roles(self):
        """Removes roles from people who are no longer supposed to see tier channels."""
        try:
            should_start_role_removal = (
                self.bot_startup_time +
                timedelta(
                    minutes=self.MOGI_LIFETIME)) <= datetime.now(
                timezone.utc)
            if should_start_role_removal:
                # Fetch all members (API call) for server to ensure role information is up-to-date
                # WARNING: if your server is large, you need a higher loop time
                # on the task
                members = [member async for member in self.GUILD.fetch_members(limit=None)]

                # Gather all discord.Role objects
                tier_roles = []
                tier_role_data = common.CONFIG["TIER_CHANNELS"]
                for tier, tier_data in tier_role_data.items():
                    tier_roles.append(self.GUILD.get_role(
                        tier_data["tier_role_id"]))

                # Create a list of all current and existing old events
                all_mogis = [ev for ev in self.old_events if ev is not None]
                if self.ongoing_event is not None:
                    all_mogis.insert(0, self.ongoing_event)

                # Create a list of all Rooms who have a valid room role
                all_rooms = []
                for mogi in all_mogis:
                    all_rooms.extend(
                        [r for r in mogi.rooms if r is not None and r.room_role is not None])

                # Go through each member in the server and, based on what roles they should have because of the
                # events they are in, remove any roles they shouldn't have
                for member in members:
                    should_have_roles = set()
                    for room in all_rooms:
                        if room.check_player(
                                member) is not None or member.id in room.subs:
                            should_have_roles.add(room.room_role)
                    should_not_have_roles = set(tier_roles) - should_have_roles
                    member_roles = set(member.roles)
                    roles_to_remove = should_not_have_roles.intersection(
                        member_roles)
                    if len(roles_to_remove) > 0:
                        try:
                            await member.remove_roles(*roles_to_remove)
                        except BaseException:
                            print(traceback.format_exc())

        except Exception as e:
            print(traceback.format_exc())

    @tasks.loop(minutes=3)
    async def check_room_threads_task(self):
        try:
            if self.ongoing_event is not None:
                await self.check_room_threads(self.ongoing_event)
        except BaseException:
            print(traceback.format_exc())

    @tasks.loop(hours=24)
    async def refresh_helper_roles(self):
        """Refreshes the helper staff role names for the /ping_staff command using the role IDs in the config"""
        try:
            helper_staff_role_ids = self.bot.config["helper_staff_roles"]
            # In my experience, large servers experience caching issues.
            # This is a forced API call which guarantees the role information
            # will be up-to-date.
            all_roles = await self.GUILD.fetch_roles()
            updated_roles = {}
            for role_id in helper_staff_role_ids:
                needle: discord.Role = discord.utils.find(
                    lambda n: role_id == n.id, all_roles)
                if needle is not None:
                    updated_roles[needle.name] = needle
            # As a fail safe, if we didn't add any new roles, either due to a misconfiguration or an internal discord
            # issue, only update if we found one or more of the roles
            if len(updated_roles) > 0:
                # We use clear and update to ensure any *references* to the
                # original dictionary are updated
                self.helper_staff_roles.clear()
                self.helper_staff_roles.update(updated_roles)
        except Exception as e:
            print(traceback.format_exc())

    @tasks.loop(minutes=10)
    async def refresh_ratings(self):
        """Refreshes the ratings"""
        try:
            await self.ratings.update_ratings()
        except Exception as e:
            print(traceback.format_exc())

    def get_event_str(self, mogi: Mogi):
        mogi_time = discord.utils.format_dt(mogi.start_time, style="F")
        mogi_time_relative = discord.utils.format_dt(
            mogi.start_time, style="R")
        return f"`#{mogi.sq_id}` **{mogi.max_player_per_team}v{mogi.max_player_per_team}:** {mogi_time} - {mogi_time_relative}"

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync(self, ctx):
        await self.bot.tree.sync()
        await ctx.send("sync'd")

    @commands.command(name="sync_server")
    @commands.is_owner()
    async def sync_server(self, ctx):
        await self.bot.tree.sync(guild=discord.Object(id=self.bot.config["guild_id"]))
        await ctx.send("sync'd")

    @commands.command(name="debug_add_team")
    @commands.is_owner()
    async def debug_add_team(self, ctx, members: commands.Greedy[discord.Member]):
        mogi = self.get_mogi(ctx)
        if mogi is None:
            return
        if (not await self.is_started(ctx, mogi)
                or not await self.is_gathering(ctx, mogi)):
            return

        member = ctx.author
        if not self.is_production:
            if common.SERVER is common.Server.MK8DX:
                member = await self.bot.fetch_user(318637887597969419)
            elif common.SERVER is common.Server.MKW:
                member = await self.bot.fetch_user(82862780591378432)
        check_players = [member]
        check_players.extend(members)
        players = self.ratings.get_rating(check_players)
        for i in range(0, 12):
            player = Player(players[0].member,
                            f"{players[0].lounge_name}{i + 1}",
                            players[0].mmr + (10 * i))
            player.confirmed = True
            squad = Team([player])
            mogi.teams.append(squad)
        msg = f"{players[0].lounge_name} added 12 times."
        await self.queue_or_send(ctx, msg)
        if self.check_close_event_change():
            await self.launch_mogi()

    @commands.command(name="debug_add_many_ratings")
    @commands.is_owner()
    async def debug_add_many_ratings(self, ctx, *ratings: str):
        mogi = self.get_mogi(ctx)
        if mogi is None:
            return
        if (not await self.is_started(ctx, mogi)
                or not await self.is_gathering(ctx, mogi)):
            return
        member = ctx.author
        if not self.is_production:
            if common.SERVER is common.Server.MK8DX:
                member = await self.bot.fetch_user(318637887597969419)
            elif common.SERVER is common.Server.MKW:
                member = await self.GUILD.fetch_member(1114699357179088917)
        for i, rating in enumerate(ratings, 1):
            if common.is_int(rating):
                player = Player(member, f"{member.name} {i}", int(
                    rating), confirmed=True)
                mogi.teams.append(Team([player]))
        msg = f"Players added with the following ratings: {' '.join(['`' + r + '`' for r in ratings])}"
        await self.queue_or_send(ctx, msg)
        if self.check_close_event_change():
            await self.launch_mogi()

    @commands.command(name="debug_add_many_players")
    @commands.is_owner()
    async def debug_add_many_players(self, ctx, members: commands.Greedy[discord.Member]):
        num_times = 25
        mogi = self.get_mogi(ctx)
        if mogi is None:
            return
        if (not await self.is_started(ctx, mogi)
                or not await self.is_gathering(ctx, mogi)):
            return

        member = ctx.author
        if not self.is_production:
            if common.SERVER is common.Server.MK8DX:
                member = await self.bot.fetch_user(318637887597969419)
            elif common.SERVER is common.Server.MKW:
                member = await self.bot.fetch_user(314861232693706752)
        check_players = [member]
        check_players.extend(members)
        players = self.ratings.get_rating(check_players)
        for i in range(0, num_times):
            player = Player(players[0].member,
                            f"{players[0].lounge_name}{i + 1}",
                            players[0].mmr + (10 * i))
            player.confirmed = True
            squad = Team([player])
            mogi.teams.append(squad)
        msg = f"{players[0].lounge_name} added {num_times} times."
        await self.queue_or_send(ctx, msg)
        if self.check_close_event_change():
            await self.launch_mogi()

    @commands.command(name="debug_start_rooms")
    @commands.is_owner()
    async def debug_start_rooms(self, ctx):
        cur_mogi = self.ongoing_event
        if cur_mogi is not None:
            await self.add_teams_to_rooms(cur_mogi, (cur_mogi.start_time.minute) % 60, True)
            return
        for old_mogi in self.old_events:
            await self.add_teams_to_rooms(old_mogi, (old_mogi.start_time.minute) % 60, True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(SquadQueue(bot))
