from pytgcalls import GroupCallFactory

class MusicPlayer:
    def __init__(self, client):
        self.group_call = GroupCallFactory(client).get_group_call()

    async def join(self, chat_id):
        await self.group_call.join(chat_id)

    async def leave(self):
        await self.group_call.leave()

    async def play(self, audio_file):
        await self.group_call.start_audio(audio_file)

    async def stop(self):
        await self.group_call.stop()
