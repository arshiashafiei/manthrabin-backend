import asyncio
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from redis.exceptions import WatchError

from manthrabin_backend import settings
from manthrabin_backend.connections import get_redis_client

from rag_utils.conversation_name import chat_name
from conversations.models import Conversation, Prompt
from documents.models import Document
from rag_utils.response_pipeline import stream
from users.models import UserInterest


class ChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.conversation = None
        self.prompts = None
        self.model_name = ""
        self.user_interests = None
        self.first_time = False
        self.user = None

        self.redis_client = get_redis_client()
        self.MAX_PROMPTS = settings.RATE_LIMIT_MAX_PROMPTS
        self.DURATION = settings.RATE_LIMIT_DURATION_SECONDS

    async def connect(self):
        self.user = self.scope.get("user", None)

        if not self.user.is_authenticated:
            print("Oh No")
            await self.close(code=4001)
            return

        conversation_public_id = self.scope["url_route"]["kwargs"]["conversation_public_id"]
        self.conversation, self.model_name = await self.is_valid_conversation(self.user, conversation_public_id)
        self.prompts = await self.conversation_prompts()
        self.user_interests = await self.get_users_interests(self.user)
        if self.conversation is None:
            print("invalid conversation")
            await self.close(code=4003)
            return

        if len(self.prompts) == 0:
            self.first_time = True
        await self.accept()

    async def disconnect(self, close_code):
        await self.close(code=close_code)

    async def receive(self, text_data):
        print("received", text_data)
        if text_data:
            if not await self.check_and_increment_rate_limit(self.user.PublicID):
                await self.send("Your Usage Limitation has been reached")
                return

            history = self.create_history(self.prompts[-10:])

            full_response = ""
            source_docs = []

            for response in stream(
                    query=text_data,
                    history=history,
                    favorites=self.user_interests,
                    model_name=self.conversation.model.name,
            ):
                print(response)
                if response.get("type") == "chunk":
                    full_response += response.get("response", "")
                elif response.get("type") == "source":
                    for source in response.get("sourcePoints", []):
                        doc_id = source.get("ID")
                        if doc_id:
                            doc = await self.get_document(doc_id)
                            if doc:
                                if not doc in source_docs:
                                    source_docs.append(doc)

            if source_docs:
                full_response += " >>>>>>>>>>>>>>> Sources:\n"
                for doc in source_docs:
                    full_response += f"{doc.title} | "

            await self.send(text_data=full_response)

            await self.send(
                text_data=json.dumps({"type": "stream_end", "conversation_id": str(self.conversation.public_id)})
            )
            print(f"Sent stream_end signal for conversation {self.conversation.public_id}")

            prompt = await self.save_message(full_response, text_data)
            self.prompts.append(prompt)

            if self.first_time and prompt is not None:
                self.first_time = False
                history = self.create_history([prompt])
                new_title = chat_name(history=history)
                await self.update_conversation(new_title)

    @database_sync_to_async
    def get_document(self, public_id):
        try:
            return Document.objects.get(public_id=public_id)
        except Document.DoesNotExist:
            return None

    @database_sync_to_async
    def is_valid_conversation(self, user, conversation_public_id):
        try:
            conversation = Conversation.objects.get(public_id=conversation_public_id, user=user)
            return conversation, conversation.model.name
        except Conversation.DoesNotExist:
            return None, None

    @database_sync_to_async
    def conversation_prompts(self):
        return list(self.conversation.prompts.all())

    @database_sync_to_async
    def get_users_interests(self, user):
        interests = UserInterest.objects.filter(UserID=user)
        return [interest.InterestID.Title for interest in interests]

    def get_chunks(self, prompt):
        history = self.create_history(self.prompts[-10:])
        return stream(
            query=prompt,
            history=history,
            favorites=self.user_interests,
            model_name=self.conversation.model_name,
        )

    @database_sync_to_async
    def save_message(self, response, text):
        created_prompt = Prompt.objects.create(
            user_prompt=text,
            response=response,
            conversation=self.conversation,
        )
        return created_prompt

    @database_sync_to_async
    def update_conversation(self, title):
        self.conversation.title = title
        self.conversation.save()

    def create_history(self, prompts):
        history = []
        for prompt in prompts:
            new_history = [
                {"Role": "user", "Message": prompt.user_prompt},
                {"Role": "assistant", "Message": prompt.response},
            ]
            history.extend(new_history)
        return history

    async def check_and_increment_rate_limit(self, user_public_id):
        if not self.redis_client:
            print("Redis client not initialized. Rate limiting bypassed.")
            return True

        key = f"rate_limit:user:{user_public_id}:prompts"

        async with self.redis_client.pipeline() as pipe:
            try:
                await pipe.watch(key)
                key_exists = await pipe.exists(key)
                pipe.multi()

                if not key_exists:
                    pipe.set(key, 1)
                    pipe.expire(key, self.DURATION)
                else:
                    pipe.incr(key)

                current_count = await pipe.get(key).execute()

                if current_count[0] > self.MAX_PROMPTS:
                    return False
                else:
                    return True

            except WatchError:
                print(f"WATCH error for user {user_public_id}, retrying transaction...")
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"An unexpected error occurred during rate limiting: {e}")
                return False
