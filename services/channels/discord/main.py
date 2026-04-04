"""
Channel — Discord Bot
Same architecture as Telegram; different SDK.

Env vars:
  DISCORD_TOKEN  — bot token
  ROUTER_URL     — default http://localhost:8001
"""
import os

import discord
import httpx

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8001")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
    print(f"Discord bot ready as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author == client.user:
        return

    async with httpx.AsyncClient() as http:
        try:
            resp = await http.post(
                f"{ROUTER_URL}/route",
                json={
                    "channel": "discord",
                    "user_id": str(message.author.id),
                    "message": message.content,
                },
                timeout=30,
            )
            resp.raise_for_status()
            reply = resp.json().get("response", "[no response]")
        except Exception as exc:
            reply = f"[router error: {exc}]"

    await message.channel.send(reply)


def main() -> None:
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
