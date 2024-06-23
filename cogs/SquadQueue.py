from __future__ import annotations

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dateutil.parser import parse
from datetime import datetime, timezone, timedelta
import time
import json
from mmr import mkw_mmr, get_mmr_from_discord_id
from mogi_objects import Mogi, Team, Player, Room, VoteView, JoinView, get_tier
import asyncio
from collections import defaultdict
from typing import Dict, List
import traceback

headers = {'Content-type': 'application/json'}

# Scheduled_Event = collections.namedtuple('Scheduled_Event', 'size time started mogi_channel')

cooldowns = defaultdict(int)

class SquadQueue(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.next_event: Mogi = None

        self.ongoing_event: Mogi = None

        self.old_events: List[Mogi] = []

        self.sq_times = []

        self._scheduler_task = self.sqscheduler.start()
        self._msgqueue_task = self.send_queued_messages.start()
        self._list_task = self.list_task.start()
        self._end_mogis_task = self.delete_old_mogis.start()

        self.msg_queue = {}

        self.list_messages = []

        self.LAUNCH_NEW_EVENTS = True

        self.GUILD = None

        self.MOGI_CHANNEL = None

        self.SUB_CHANNEL = None

        self.LIST_CHANNEL = None

        self.HISTORY_CHANNEL = None

        self.GENERAL_CHANNEL = None

        self.LOCK = asyncio.Lock()

        self.URL = bot.config["url"]

        self.MOGI_LIFETIME = bot.config["MOGI_LIFETIME"]

        self.SUB_MESSAGE_LIFETIME_SECONDS = bot.config["SUB_MESSAGE_LIFETIME_SECONDS"]

        self.room_mmr_threshold = bot.config["ROOM_MMR_THRESHOLD"]

        self.TRACK_TYPE = bot.config["track_type"]

        self.TIER_INFO = []

        # Time between each event queue opening
        self.QUEUE_OPEN_TIME = timedelta(minutes=bot.config["QUEUE_OPEN_TIME"])

        # number of minutes after QUEUE_OPEN_TIME that teams can join the mogi
        self.JOINING_TIME = timedelta(minutes=bot.config["JOINING_TIME"])

        # number of minutes after JOINING_TIME for any potential extra teams to join
        self.EXTENSION_TIME = timedelta(minutes=bot.config["EXTENSION_TIME"])

        assert (self.QUEUE_OPEN_TIME >= self.JOINING_TIME + self.EXTENSION_TIME)

        # This is the first event of the day's time. However, this isn't the first event the bot will run. This is literally the time of the first event in a daily schedule.
        self.FIRST_EVENT_TIME = datetime.combine(datetime.utcnow().date(), datetime.min.time(),
                                                 tzinfo=timezone.utc) + timedelta(
            minutes=bot.config["FIRST_EVENT_TIME"])

        with open('./timezones.json', 'r') as cjson:
            self.timezones = json.load(cjson)

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
        await self.get_ladder_info()
        try:
            await self.LIST_CHANNEL.purge()
        except:
            print("Purging List channel failed", flush=True)
            print(traceback.format_exc())
        try:
            await self.SUB_CHANNEL.purge()
        except:
            print("Purging Sub channel failed", flush=True)
            print(traceback.format_exc())
        print(f"Server - {self.GUILD}", flush=True)
        print(f"Join Channel - {self.MOGI_CHANNEL}", flush=True)
        print(f"Sub Channel - {self.SUB_CHANNEL}", flush=True)
        print(f"List Channel - {self.LIST_CHANNEL}", flush=True)
        print(f"History Channel - {self.HISTORY_CHANNEL}", flush=True)
        print(f"General Channel - {self.GENERAL_CHANNEL}", flush=True)
        print("Ready!", flush=True)
    

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

    # goes thru the msg queue for each channel and combines them
    # into as few messsages as possible, then sends them
    @tasks.loop(seconds=2)
    async def send_queued_messages(self):
        try:
            for channel in self.msg_queue.keys():
                channel_queue = self.msg_queue[channel]
                sentmsgs = []
                msg = ""
                for i in range(len(channel_queue)-1, -1, -1):
                    msg = channel_queue.pop(i) + "\n" + msg
                    if len(msg) > 1500:
                        sentmsgs.append(msg)
                        msg = ""
                if len(msg) > 0:
                    sentmsgs.append(msg)
                for i in range(len(sentmsgs)-1, -1, -1):
                    await channel.send(sentmsgs[i])
        except Exception as e:
            print(traceback.format_exc())

    def get_mogi(self, ctx: discord.Interaction | discord.ext.commands.context.Context) -> Mogi | None:
        if self.ongoing_event is None:
            return None
        if self.ongoing_event.mogi_channel.id == ctx.channel.id:
            return self.ongoing_event

    def is_staff(self, member: discord.Member):
        return any(member.get_role(staff_role) for staff_role in self.bot.config["staff_roles"])

    async def is_started(self, ctx, mogi: Mogi):
        if not mogi.started:
            await ctx.send("Mogi has not been started yet... type !start")
        return mogi.started

    async def is_gathering(self, ctx, mogi: Mogi):
        if not mogi.gathering:
            await ctx.send("Mogi is closed; players cannot join or drop from the event")
        return mogi.gathering

    @app_commands.command(name="c")
    @app_commands.guild_only()
    async def can(self, interaction: discord.Interaction):
        """Join a mogi"""
        await interaction.response.defer()
        async with self.LOCK:
            member = interaction.user
            mogi = self.get_mogi(interaction)
            if mogi is None or not mogi.started or not mogi.gathering:
                await interaction.followup.send("Queue has not started yet.")
                return

            player_team = mogi.check_player(member)

            if player_team is not None:
                await interaction.followup.send(f"{interaction.user.mention} is already signed up.")
                return

            msg = ""
            placement_role_id = 723753340063842345
            if member.get_role(placement_role_id):
                starting_player_mmr = 750
                players = [Player(member, member.display_name, starting_player_mmr)]
                msg += f"{players[0].lounge_name} is assumed to be a new player and will be playing this mogi with a starting MMR of {starting_player_mmr}.  "
                msg += "If you believe this is a mistake, please contact a staff member for help.\n"
            else:    
                players = await mkw_mmr(self.URL, [member], self.TRACK_TYPE)

            if len(players) == 0 or players[0] is None:
                msg = f"{interaction.user.mention} fetch for MMR has failed and joining the queue was unsuccessful.  "
                msg += "Please try again.  If the problem continues then contact a staff member for help."
                await interaction.followup.send(msg)
                return

            players[0].confirmed = True
            squad = Team(players)
            mogi.teams.append(squad)

            msg += f"{players[0].lounge_name} joined queue closing at {discord.utils.format_dt(mogi.start_time)}, `[{mogi.count_registered()} players]`"

            await interaction.followup.send(msg)
            await self.check_room_channels(mogi)
            await self.check_num_teams(mogi)

    @app_commands.command(name="d")
    @app_commands.guild_only()
    async def drop(self, interaction: discord.Interaction):
        """Remove user from mogi"""
        await interaction.response.defer()
        async with self.LOCK:
            mogi = self.get_mogi(interaction)
            if mogi is None or not mogi.started or not mogi.gathering:
                await interaction.followup.send("Queue has not started yet.")
                return

            member = interaction.user
            squad = mogi.check_player(member)
            if squad is None:
                await interaction.followup.send(f"{member.display_name} is not currently in this event; type `/c` to join")
                return
            mogi.teams.remove(squad)
            msg = "Removed "
            msg += ", ".join([p.lounge_name for p in squad.players])
            msg += f" from the mogi {discord.utils.format_dt(mogi.start_time, style='R')}"
            msg += f", `[{mogi.count_registered()} players]`"

            await interaction.followup.send(msg)
            await self.check_num_teams(mogi)

    @app_commands.command(name="sub")
    @app_commands.guild_only()
    # @commands.cooldown(rate=1, per=120, type=commands.BucketType.user)
    async def sub(self, interaction: discord.Interaction):
        """Sends out a request for a sub in the sub channel. Only works in thread channels for SQ rooms."""
        current_time = time.time()
        lastCommandTime = cooldowns.get(interaction.user.id)
        print(lastCommandTime, flush=True)
        if lastCommandTime == None:
            lastCommandTime = 0

        if (current_time - lastCommandTime) < 120 and not self.is_staff(interaction.user):  # Cooldown timer in seconds
            await interaction.response.send_message(
                f"You are still on cooldown. Please wait for {int(2 * 60 - (current_time - lastCommandTime))} more seconds to use this command again.",
                ephemeral=True)
            return
        
        is_room_thread = False
        room = None
        bottom_room_num = 1
        mogi = self.ongoing_event
        if mogi is not None:
            if mogi.is_room_thread(interaction.channel_id):
                room = mogi.get_room_from_thread(interaction.channel_id)
                bottom_room_num = len(mogi.rooms)
                is_room_thread = True
        for old_mogi in self.old_events:
            if old_mogi.is_room_thread(interaction.channel.id):
                room = old_mogi.get_room_from_thread(interaction.channel.id)
                bottom_room_num = len(old_mogi.rooms)
                is_room_thread = True
                break
        if not is_room_thread:
            await interaction.response.send_message(
                f"More than {self.MOGI_LIFETIME} minutes have passed since mogi start, the Mogi Object has been deleted.",
                ephemeral=True)
            return
        msg = "<@&1167985222533533817> - "
        if bottom_room_num == 1:
            msg += f"Room 1 is looking for a sub with any mmr\n"
        elif room.room_num == 1:
            msg += f"Room 1 is looking for a sub with mmr >{room.mmr_low - 500}\n"
        elif room.room_num == bottom_room_num:
            msg += f"Room {room.room_num} is looking for a sub with mmr <{room.mmr_high + 500}\n"
        else:
            msg += f"Room {room.room_num} is looking for a sub with range {room.mmr_low - 500}-{room.mmr_high + 500}\n"
        message_delete_date = datetime.now(
            timezone.utc) + timedelta(seconds=self.SUB_MESSAGE_LIFETIME_SECONDS)
        msg += f"Message will auto-delete in {discord.utils.format_dt(message_delete_date, style='R')}"
        await self.SUB_CHANNEL.send(msg, delete_after=self.SUB_MESSAGE_LIFETIME_SECONDS)
        view = JoinView(room, get_mmr_from_discord_id, bottom_room_num)
        await self.SUB_CHANNEL.send(view=view, delete_after=self.SUB_MESSAGE_LIFETIME_SECONDS)
        cooldowns[interaction.user.id] = current_time  # Updates cooldown
        await interaction.response.send_message("Sent out request for sub.")

    @tasks.loop(seconds=30)
    async def list_task(self):
        """Continually display the list of confirmed players for a mogi in the history channel"""
        mogi = self.ongoing_event
        if mogi is not None:
            if not mogi.gathering:
                await self.delete_list_messages(0)
                return

            mogi_list = mogi.confirmed_list()

            # Remove late players from the list to display separately
            full_list_length = len(mogi_list)
            num_of_rooms = full_list_length // 12
            num_confirmed_players = num_of_rooms * 12
            num_late_players = full_list_length - num_confirmed_players
            late_players = []
            for i in range(num_confirmed_players, full_list_length):
                player = mogi_list.pop()
                late_players.append(player)

            sorted_mogi_list = sorted(mogi_list, reverse=True)
            msg = f"**Last Updated:** {discord.utils.format_dt(datetime.now(timezone.utc), style='R')}\n\n"
            msg += "**Current Mogi List:**\n\n"
            msg += f"**Queue closing: {discord.utils.format_dt(mogi.start_time)}**\n"
            for i in range(len(sorted_mogi_list)):
                msg += f"{i + 1}) "
                msg += ", ".join([p.lounge_name for p in sorted_mogi_list[i].players])
                msg += f" ({sorted_mogi_list[i].players[0].mmr} MMR)\n"
                if ((i + 1) % 12 == 0):
                    msg += "ㅤ\n"
            msg += "**Late Players:**\n"
            for i in range(len(late_players)):
                msg += f"{i + 1}) "
                msg += ", ".join([p.lounge_name for p in late_players[i].players])
                msg += f" ({late_players[i].players[0].mmr} MMR)\n"
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
            except:
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not (message.content.isdecimal() and 12 <= int(message.content) <= 180):
            return
        mogi = None
        if self.ongoing_event is not None:
            mogi = self.ongoing_event if self.ongoing_event.is_room_thread(message.channel.id) else None
        if mogi is None:
            mogi = discord.utils.find(lambda m: m.is_room_thread(message.channel.id), self.old_events)
        if mogi is None:
            return
        room = discord.utils.find(lambda r: r.thread.id == message.channel.id, mogi.rooms)
        if room is None or not room.teams:
            return
        team = discord.utils.find(lambda t: t.has_player(message.author), room.teams)
        if team is None:
            return
        player = discord.utils.find(lambda p: p.member.id == message.author.id, team.players)
        if player is not None:
            player.score = int(message.content)

    @app_commands.command(name="remove_player")
    @app_commands.guild_only()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        """Removes a specific player from the current queue.  Staff use only."""
        await interaction.response.defer()
        async with self.LOCK:
            mogi = self.get_mogi(interaction)
            if mogi is None or not mogi.started or not mogi.gathering:
                await interaction.followup.send("Queue has not started yet.")
                return

            squad = mogi.check_player(member)
            if squad is None:
                await interaction.followup.send(f"{member.display_name} is not currently in this event; type `/c` to join")
                return
            mogi.teams.remove(squad)
            msg = "Staff has removed "
            msg += ", ".join([p.lounge_name for p in squad.players])
            msg += f" from the mogi {discord.utils.format_dt(mogi.start_time, style='R')}"
            msg += f", `[{mogi.count_registered()} players]`"

            await interaction.followup.send(msg)

    @app_commands.command(name="annul_current_mogi")
    @app_commands.guild_only()
    async def annul_current_mogi(self, interaction: discord.Interaction):
        """The mogi currently gathering will be deleted.  The queue resumes at the next hour.  Staff use only."""
        self.next_event = None
        self.ongoing_event = None
        await self.lockdown(self.MOGI_CHANNEL)
        await interaction.response.send_message("The current Mogi has been canceled, the queue will resume at the next hour.")

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
    
    @app_commands.command(name="change_event_time")
    @app_commands.guild_only()
    async def change_event_time(self, interaction: discord.Interaction, event_time: int):
        """Change the amount of time for each event in the queue."""
        if event_time > 15:
            self.QUEUE_OPEN_TIME = timedelta(minutes=event_time)
            self.JOINING_TIME = timedelta(minutes=event_time) - self.EXTENSION_TIME
            await interaction.response.send_message(
                f"The amount of time for each mogi has been changed to {event_time} minutes.")
        else:
            await interaction.response.send_message("Please enter a number of minutes greater than 15.")

    @app_commands.command(name="change_mmr_threshold")
    @app_commands.guild_only()
    async def change_mmr_threshold(self, interaction: discord.Interaction, mmr_threshold: int):
        """Change the maximum MMR gap allowed for a room."""
        self.room_mmr_threshold = mmr_threshold
        await interaction.response.send_message(f"MMR Threshold for Queue Rooms has been modified to {mmr_threshold} MMR.")

    @app_commands.command(name="peek_bot_config")
    @app_commands.guild_only()
    async def peek_bot_config(self, interaction: discord.Interaction):
        """View the configured values for the Queue System."""
        msg = ""
        msg += f"Time between each event: {self.QUEUE_OPEN_TIME}, \n"
        msg += f"Room MMR Threshold: {self.room_mmr_threshold}"
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

    @commands.command(name="schedule_sq_times")
    @commands.guild_only()
    async def schedule_sq_times(self, ctx, timestamps: commands.Greedy[int]):
        """Saves a list of sq times to skip over.  Input a list of unix utc timestamps.  Staff use only."""
        if not await self.has_roles(ctx.author, ctx.guild.id, ctx.bot.config):
            return
        await self.queue_or_send(ctx,
                                 "This command has been disabled as it's unclear how the new changes might affect this system.")
        return

        msg = "List of new Dates:\n"
        curr_time = datetime.now(timezone.utc)
        new_sq_dates = []

        for timestamp in timestamps:
            date = datetime.fromtimestamp(timestamp, timezone.utc)
            truncated_date = date.replace(
                minute=0, second=0, microsecond=0)

            if curr_time > truncated_date:
                msg = ""
                msg += f"Timestamp {timestamp} represents {truncated_date} and is in the past, submit a future date.\n"
                msg += "No timestamps from this usage of the command have been added."
                await self.queue_or_send(ctx, msg)
                return

            new_sq_dates.append(truncated_date)
            msg += f"{truncated_date}\n"

        self.sq_times += new_sq_dates
        self.sq_times = list(set(self.sq_times))

        list.sort(self.sq_times)

        await self.queue_or_send(ctx, msg)

    @app_commands.command(name="peek_sq_times")
    @app_commands.guild_only()
    async def peek_sq_times(self, interaction: discord.Interaction):
        """Peeks the current list of sq times.  Staff use only."""
        msg = "List of Squad Queue Times:\n"

        for index, date in enumerate(self.sq_times):
            msg += f"{index + 1}) {date}\n"

        await interaction.response.send_message(msg)

    @app_commands.command(name="clear_sq_times")
    @app_commands.guild_only()
    async def clear_sq_times(self, interaction: discord.Interaction):
        """Clears current list of sq times.  Staff use only."""
        self.sq_times = []

        await interaction.response.send_message("Cleared list of Squad Queue Times.")
    
    @app_commands.command(name="update_tier_info")
    @app_commands.guild_only()
    async def update_tier_info(self, interaction: discord.Interaction):
        """Updates the info on each tier"""
        #await self.get_ladder_info()
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

    # check if user has roles defined in config.json
    async def has_roles(self, member: discord.Member, guild_id: int, config):
        if str(guild_id) not in config["admin_roles"].keys():
            return True
        for role in member.roles:
            if role.name in config["admin_roles"][str(guild_id)]:
                return True
        return False

    @staticmethod
    async def end_voting(mogi: Mogi):
        """Ends voting in all rooms with ongoing votes."""
        try:
            if mogi is not None:
                for index, room in enumerate(mogi.rooms, 1):
                    if not room or not room.view:
                        print(f"Skipping room {index} in function end_voting.", flush=True)
                        continue
                    await room.view.find_winner()
        except Exception as e:
            print(traceback.format_exc())

    @staticmethod
    async def write_history(mogi: Mogi, history_channel: discord.TextChannel):
        """Writes the teams, tier and average of each room per hour."""
        try:
            if mogi is not None:
                await history_channel.send(f"{discord.utils.format_dt(mogi.start_time)} Rooms")
                for index, room in enumerate(mogi.rooms, 1):
                    if not room or not room.view:
                        print(f"Skipping room {index} in function write_history.", flush=True)
                        continue
                    msg = room.view.header_text
                    msg += f"{room.thread.jump_url}\n"
                    msg += room.view.teams_text
                    msg += "ㅤ"
                    await history_channel.send(msg)
        except Exception as e:
            print(traceback.format_exc())

    # make thread channels while the event is gathering instead of at the end,
    # since discord only allows 50 thread channels to be created per 5 minutes.
    async def check_room_channels(self, mogi):
        num_teams = mogi.count_registered()
        num_rooms = int(num_teams / (12 / mogi.players_per_team))
        num_created_rooms = len(mogi.rooms)
        if num_created_rooms >= num_rooms:
            return
        for i in range(num_created_rooms, num_rooms):
            minute = mogi.start_time.minute
            if len(str(minute)) == 1:
                minute = '0' + str(minute)
            room_name = f"{mogi.start_time.month}/{mogi.start_time.day}, {mogi.start_time.hour}:{minute}:00 - Room {i+1}"
            try:
                room_channel = await self.GENERAL_CHANNEL.create_thread(name=room_name,
                                                                        auto_archive_duration=60,
                                                                        invitable=False)
            except Exception as e:
                print(traceback.format_exc())
                err_msg = f"\nAn error has occurred while creating a room channel:\n{e}"
                await mogi.mogi_channel.send(err_msg)
                return
            mogi.rooms.append(Room(None, i+1, room_channel))

    @staticmethod
    async def handle_voting_and_history(mogi: Mogi, history_channel: discord.TextChannel):
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
        num_rooms = int(mogi.count_registered() / (12 / mogi.players_per_team))
        if num_rooms == 0:
            self.ongoing_event = None
            await mogi.mogi_channel.send(f"Not enough players to fill a single room! This mogi will be cancelled.")
            return

        mogi.making_rooms_run = True
        if mogi.gathering:
            mogi.gathering = False
            await mogi.mogi_channel.send("Mogi is now closed; players can no longer join or drop from the event")
        await self.lockdown(mogi.mogi_channel)

        teams_per_room = int(12 / mogi.players_per_team)
        num_teams = int(num_rooms * teams_per_room)
        final_list = mogi.confirmed_list()[0:num_teams]
        sorted_list = sorted(final_list, reverse=True)

        extra_members = []
        if str(mogi.mogi_channel.guild.id) in self.bot.config["members_for_channels"].keys():
            extra_members_ids = self.bot.config["members_for_channels"][str(
                mogi.mogi_channel.guild.id)]
            for m in extra_members_ids:
                extra_members.append(mogi.mogi_channel.guild.get_member(m))
        if str(mogi.mogi_channel.guild.id) in self.bot.config["roles_for_channels"].keys():
            extra_roles_ids = self.bot.config["roles_for_channels"][str(
                mogi.mogi_channel.guild.id)]
            for r in extra_roles_ids:
                extra_members.append(mogi.mogi_channel.guild.get_role(r))

        rooms = mogi.rooms
        for i in range(num_rooms):
            msg = f"`Room {i+1} - Player List`\n"
            mentions = ""
            start_index = i*teams_per_room
            player_list = []
            for j in range(teams_per_room):
                msg += f"`{j+1}.` "
                team = sorted_list[start_index+j]
                player_list.append(sorted_list[start_index+j].get_first_player())
                msg += ", ".join([p.lounge_name for p in team.players])
                msg += f" ({int(team.avg_mmr)} MMR)\n"
                mentions += " ".join([p.member.mention for p in team.players])
                mentions += " "
            room_msg = msg
            mentions += " ".join([m.mention for m in extra_members if m is not None])
            room_msg += "\nVote for format FFA, 2v2, 3v3, 4v4, or 6v6.\n"
            room_msg += mentions
            curr_room = rooms[i]
            room_channel = curr_room.thread
            curr_room.teams = sorted_list[start_index:start_index+teams_per_room]
            curr_room.mmr_low = player_list[11].mmr
            curr_room.mmr_high = player_list[0].mmr
            if curr_room.mmr_high - curr_room.mmr_low > self.room_mmr_threshold:
                msg += f"\nThe mmr gap in the room is higher than the allowed threshold of {self.room_mmr_threshold} MMR, this room has been cancelled."
            else:
                try:
                    await room_channel.send(room_msg)
                    view = VoteView(player_list, room_channel, mogi, self.TIER_INFO)
                    curr_room.view = view
                    await room_channel.send(view=view)
                except Exception as e:
                    print(traceback.format_exc())
                    err_msg = f"\nAn error has occurred while creating the room channel; please contact your opponents in DM or another channel\n"
                    err_msg += mentions
                    msg += err_msg
                    room_channel = None
            try:
                await mogi.mogi_channel.send(msg)
            except Exception as e:
                print(
                    f"Mogi Channel message for room {i+1} has failed to send.", flush=True)
                print(traceback.format_exc())
        if num_teams < mogi.count_registered():
            missed_teams = mogi.confirmed_list(
            )[num_teams:mogi.count_registered()]
            msg = "`Late players:`\n"
            for i in range(len(missed_teams)):
                msg += f"`{i+1}.` "
                msg += ", ".join([p.lounge_name for p in missed_teams[i].players])
                msg += f" ({int(missed_teams[i].avg_mmr)} MMR)\n"
            try:
                await mogi.mogi_channel.send(msg)
            except Exception as e:
                print("Late Player message has failed to send.", flush=True)
                print(traceback.format_exc())

        asyncio.create_task(SquadQueue.handle_voting_and_history(self.ongoing_event, self.HISTORY_CHANNEL))
        self.old_events.append(self.ongoing_event)
        self.ongoing_event = None

    async def check_num_teams(self, mogi: Mogi):
        if not mogi.gathering or not mogi.is_automated:
            return
        cur_time = datetime.now(timezone.utc)
        if mogi.start_time <= cur_time:
            num_leftover_teams = mogi.count_registered() % int((12 / mogi.players_per_team))
            if num_leftover_teams == 0:
                mogi.gathering = False
                await self.lockdown(mogi.mogi_channel)
                await mogi.mogi_channel.send(
                    "A sufficient amount of players has been reached, so the mogi has been closed to extra players. Rooms will be made within the next minute.")

    async def ongoing_mogi_checks(self):
        mogi = self.ongoing_event
        if mogi is not None:
            # If it's not automated, not started, we've already started making the rooms, don't run this
            async with self.LOCK:
                if not mogi.is_automated or not mogi.started or mogi.making_rooms_run:
                    return
                cur_time = datetime.now(timezone.utc)
                if (mogi.start_time + self.EXTENSION_TIME) <= cur_time:
                    mogi.gathering = False
                elif mogi.start_time <= cur_time and mogi.gathering:
                    # check if there are an even amount of teams since we are past the queue time
                    num_leftover_teams = mogi.count_registered() % int((12 / mogi.players_per_team))
                    if num_leftover_teams == 0:
                        mogi.gathering = False
                    else:
                        if int(cur_time.second / 20) == 0:
                            force_time = mogi.start_time + self.EXTENSION_TIME
                            minutes_left = (force_time - cur_time).seconds // 60
                            x_teams = int(int(12 / mogi.players_per_team) - num_leftover_teams)
                            await mogi.mogi_channel.send(
                                f"Need {x_teams} more player(s) to start immediately. Starting in {minutes_left + 1} minute(s) regardless.")
            if not mogi.gathering:
                await self.delete_list_messages(0)
                await mogi.mogi_channel.send("Mogi is now closed; players can no longer join or drop from the event")
                await self.add_teams_to_rooms(mogi, (mogi.start_time.minute) % 60, True)

    async def scheduler_mogi_start(self):
        cur_time = datetime.now(timezone.utc)
        next_mogi = self.next_event
        if next_mogi is not None and (next_mogi.start_time - self.JOINING_TIME) < cur_time:
            self.next_event = None  # We are trying to launch the event - fail or not, we set next event to None
            # There is an ongoing event, but people are still queueing, so remove it and fail
            if self.ongoing_event is not None and self.ongoing_event.gathering:
                await next_mogi.mogi_channel.send(
                    f"Because there is an ongoing event right now, the following event has been removed:\n{self.get_event_str(next_mogi)}\n")
                return
            # There is an ongoing event, but it has already started, so add it to the old events
            if self.ongoing_event is not None and self.ongoing_event.started:  # This is potentially an issue - we should be checking if make rooms has been run, not if it's started...?
                self.old_events.append(self.ongoing_event)

            # Put the next mogi as the current event and launch it
            self.ongoing_event = next_mogi
            self.ongoing_event.started = True
            self.ongoing_event.gathering = True
            await self.unlockdown(self.ongoing_event.mogi_channel)
            await self.ongoing_event.mogi_channel.send(
                f"A queue is gathering for the mogi {discord.utils.format_dt(self.ongoing_event.start_time, style='R')} - Type `/c` to join, and `/d` to drop.")

    @tasks.loop(seconds=20.0)
    async def sqscheduler(self):
        """Scheduler that checks if it should start mogis and close them"""
        # It may seem silly to do try/except Exception, but this coroutine **cannot** fail
        # This coroutine *silently* fails and stops if exceptions aren't caught - an annoying abtraction of asyncio
        # This is unacceptable considering people are relying on these mogis to run, so we will not allow this routine to stop
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
            await self.ongoing_mogi_checks()
        except Exception as e:
            print(traceback.format_exc())

    def compute_next_event_open_time(self):
        cur_time = datetime.now(timezone.utc)
        time_elapsed = cur_time - self.FIRST_EVENT_TIME
        num_launched_events = time_elapsed // self.QUEUE_OPEN_TIME
        next_event_open_time = self.FIRST_EVENT_TIME + (self.QUEUE_OPEN_TIME * num_launched_events)
        return next_event_open_time

    async def schedule_que_event(self):
        """Schedules next queue in the SquadQueue mogi queueing channel."""
        if self.GUILD is not None:
            if not self.LAUNCH_NEW_EVENTS:
                # (f"Not allowed to launch new events.", flush=True)
                return
            next_event_open_time = self.compute_next_event_open_time()
            next_event_start_time = next_event_open_time + self.JOINING_TIME
            print(f"Next event open time: {next_event_open_time}", flush=True)
            # We don't want to schedule the next event if it would open after it's joining period and during its extension period
            if next_event_start_time < datetime.now(timezone.utc):
                return
            if len(self.sq_times) > 0 and next_event_open_time == self.sq_times[0]:
                self.sq_times.pop(0)
                await self.MOGI_CHANNEL.send(
                    "Squad Queue is currently going on at this hour!  The queue will remain closed.")

            self.next_event = Mogi(sq_id=1,
                                   players_per_team=1,
                                   teams_per_room=12,
                                   mogi_channel=self.MOGI_CHANNEL,
                                   is_automated=True,
                                   start_time=next_event_start_time)

            print(f"Started Queue for {next_event_start_time}", flush=True)

    @tasks.loop(minutes=1)
    async def delete_old_mogis(self):
        """Deletes old mogi objects"""
        try:
            curr_time = datetime.now(timezone.utc)
            mogi_lifetime = timedelta(minutes=self.MOGI_LIFETIME)
            delete_queue = [m for m in self.old_events if (curr_time - mogi_lifetime) > m.start_time]
            for mogi in delete_queue:
                print(f"Deleting {mogi.start_time} Mogi at {curr_time}", flush=True)
                self.old_events.remove(mogi)
        except Exception as e:
            print(traceback.format_exc())

    def get_event_str(self, mogi: Mogi):
        mogi_time = discord.utils.format_dt(mogi.start_time, style="F")
        mogi_time_relative = discord.utils.format_dt(
            mogi.start_time, style="R")
        return f"`#{mogi.sq_id}` **{mogi.players_per_team}v{mogi.players_per_team}:** {mogi_time} - {mogi_time_relative}"

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

        check_players = [ctx.author]
        check_players.extend(members)
        players = await mkw_mmr(self.URL, check_players, self.TRACK_TYPE)
        for i in range(0, 12):
            player = Player(
                players[0].member, f"{players[0].lounge_name}{i + 1}", players[0].mmr + (10 * i))
            player.confirmed = True
            squad = Team([player])
            mogi.teams.append(squad)
        msg = f"{players[0].lounge_name} added 12 times."
        await self.queue_or_send(ctx, msg)
        await self.check_room_channels(mogi)
        await self.check_num_teams(mogi)

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

        check_players = [ctx.author]
        check_players.extend(members)
        players = await mkw_mmr(self.URL, check_players, self.TRACK_TYPE)
        for i in range(0, num_times):
            player = Player(
                players[0].member, f"{players[0].lounge_name}{i + 1}", players[0].mmr + (10 * i))
            player.confirmed = True
            squad = Team([player])
            mogi.teams.append(squad)
        msg = f"{players[0].lounge_name} added {num_times} times."
        await self.queue_or_send(ctx, msg)
        await self.check_room_channels(mogi)
        await self.check_num_teams(mogi)

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


async def setup(bot):
    await bot.add_cog(SquadQueue(bot))


    

