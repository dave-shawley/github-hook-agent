import rejected.models


class AgentConsumer(rejected.FunctionalConsumer):
    async def process(self, ctx: rejected.models.ProcessingContext) -> None:
        raise NotImplementedError
