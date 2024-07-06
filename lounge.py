import common
import discord
from discord.ext import commands
import json
import asyncio

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=['!', '^'],
                   case_insensitive=True, intents=intents, help_command=None)

initial_extensions = ['cogs.SquadQueue']
bot.config = common.config


@bot.event
async def on_ready():
    print("Logged in as {0.user}".format(bot), flush=True)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await (await ctx.send("Your command is missing an argument: `%s`" %
                              str(error.param))).delete(delay=10)
        return
    if isinstance(error, commands.CommandOnCooldown):
        await (await ctx.send("This command is on cooldown; try again in %.0fs"
                              % error.retry_after)).delete(delay=5)
        return
    if isinstance(error, commands.MissingAnyRole):
        await (await ctx.send("You need one of the following roles to use this command: `%s`"
                              % (", ".join(error.missing_roles)))
               ).delete(delay=10)
        return
    if isinstance(error, commands.BadArgument):
        await (await ctx.send("BadArgument Error: `%s`" % error.args)).delete(delay=10)
        return
    if isinstance(error, commands.BotMissingPermissions):
        await (await ctx.send("I need the following permissions to use this command: %s"
                              % ", ".join(error.missing_perms))).delete(delay=10)
        return
    if isinstance(error, commands.NoPrivateMessage):
        await (await ctx.send("You can't use this command in DMs!")).delete(delay=5)
        return
    raise error


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.errors.CommandOnCooldown):
        await interaction.response.send_message(f'You are on cooldown. Try again in {round(error.retry_after)}s', ephemeral=True)
    else:
        raise error

@bot.event
async def setup_hook():
    for extension in initial_extensions:
        await bot.load_extension(extension)

bot.run(bot.config["token"])

# async def main():
#     async with bot:
#         for extension in initial_extensions:
#             await bot.load_extension(extension)
#         await bot.start(bot.config["token"])

# asyncio.run(main())
