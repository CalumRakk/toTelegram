from .telegram import TELEGRAM

client= TELEGRAM.get_client

@client.on_message()
def echo(client, message):
    message.reply(message.text)

client.run()