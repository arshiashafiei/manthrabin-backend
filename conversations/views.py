import uuid
import logging
from smtplib import SMTPException

from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics, status, viewsets
from django.db import models
from django.core.exceptions import ValidationError
from django_elasticsearch_dsl.search import Search
from drf_spectacular.utils import extend_schema

from manthrabin_backend import settings
from .models import Conversation, Prompt, SharedConversation, LLMModel
from .serializers import ConversationSearchSerializer, ConversationSerializer, PromptSearchSerializer, PromptSerializer, LLMModelSerializer
from rest_framework.pagination import LimitOffsetPagination
from rag_utils.chat_util import simple_chat
from .documents import PromptDocument

logger = logging.getLogger(__name__)

class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'public_id'
    pagination_class = LimitOffsetPagination

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class PromptsListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PromptSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        conversation_id = self.kwargs['conversation_id']
        conversation = get_object_or_404(Conversation, public_id=conversation_id, user=self.request.user)
        return Prompt.objects.filter(conversation=conversation)


class PromptCreateView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PromptSerializer

    def perform_create(self, serializer):
        conversation_id = self.kwargs['conversation_id']
        conversation = get_object_or_404(Conversation, public_id=conversation_id, user=self.request.user)

        if conversation.user != self.request.user:
            raise PermissionDenied("invalid conversation.")
        prompt = serializer.validated_data.get('user_prompt')
        response = simple_chat(prompt, conversation.public_id)
        serializer.save(conversation=conversation, response=response)

class CreateConversationLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, **kwargs):
        conversation_public_id= kwargs.get('conversation_id')
        conversation = get_object_or_404(Conversation, public_id=conversation_public_id)
        if conversation.user != self.request.user:
            raise PermissionDenied("invalid conversation.")
        share_link = self._create_or_update_shared_conversation(conversation)
        return Response(
            {
                "share_link": str(share_link.public_id),
                "conversation_id": str(conversation.public_id),
            },
            status=200)

    def post(self, request, **kwargs):
        email = request.data.get('email')
        conversation_public_id = kwargs.get('conversation_id')
        conversation = get_object_or_404(Conversation, public_id=conversation_public_id)
        if conversation.user != self.request.user:
            return Response(
                ({"error": "conversation not found"}),
                status=404
            )
        share_link =  self._create_or_update_shared_conversation( conversation)
        link=f"manthrabin.ir/share/{share_link.public_id}"

        try:
            send_mail(
                subject= f"Manthrabin Shared Conversation",
                message=f"Click the link below to access conversation :\n{link}",
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[email],
                fail_silently=False,

            )

            return Response({
                "message": f"Share link sent to {email}."
            }, status=200)

        except Exception as e:
            print(f"SMTP error occurred: {e}")
            return Response(
                {"error": "Failed to send email.", "details": str(e)},
                status=502
            )
    def _create_or_update_shared_conversation(self, conversation):
        last_prompt = conversation.prompts.first()
        try:
            share_link = SharedConversation.objects.get(conversation=conversation)
            if share_link.last_prompt != last_prompt:
                share_link.last_prompt = last_prompt
                share_link.public_id = uuid.uuid4()
                share_link.save()
        except SharedConversation.DoesNotExist:
            share_link = SharedConversation.objects.create(
                conversation=conversation,
                last_prompt=last_prompt,
            )
            share_link.save()
        return share_link



class ShareConversationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, **kwargs):
        shared_conversation = get_object_or_404(SharedConversation, public_id=kwargs.get('share_id'))
        original_conversation = shared_conversation.conversation
        new_conversation = Conversation.objects.create(
            user=request.user,
            model= original_conversation.model,
            title = original_conversation.title
        )
        prompts_to_copy = Prompt.objects.filter(
            conversation=original_conversation,
            time__lte=shared_conversation.last_prompt.time
        )

        new_prompts = []
        for prompt in prompts_to_copy:
            new_prompts.append(Prompt(
                conversation=new_conversation,
                user_prompt=prompt.user_prompt,
                response=prompt.response,
                ))
        Prompt.objects.bulk_create(new_prompts)

        copied_prompts_qs = Prompt.objects.filter(conversation=new_conversation)
        paginator = LimitOffsetPagination()
        paginated = paginator.paginate_queryset(copied_prompts_qs, request)
        serializer = PromptSerializer(paginated, many=True)
        return paginator.get_paginated_response(serializer.data)


@extend_schema(operation_id="search_conversations")
class ConversationSearchView(generics.ListAPIView):
    """
    .
    returns list of { conversation_id, title, matching_prompts[...] }
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationSearchSerializer

    def get_queryset(self):
        try:
            q = self.request.GET.get('q', '').strip()
            if not q:
                raise ValidationError({"q": "Query parameter 'q' is required."})

            s = (
                PromptDocument.search()
                .query("multi_match", query=q, fields=["user_prompt", "response"])
                .filter("term", user_id=self.request.user.id)
                .source(["conversation_public_id", "public_id", "user_prompt", "response", "time"])
            )
            response = s.execute()
            
            # Group results by conversation
            conversation_data = {}
            for hit in response.hits:
                conv_id = hit.conversation_public_id
                if conv_id not in conversation_data:
                    conversation_data[conv_id] = {
                        "matching_prompts": []
                    }
                conversation_data[conv_id]["matching_prompts"].append({
                    "prompt_id": hit.public_id,
                    "user_prompt": hit.user_prompt,
                    "response": hit.response,
                    "time": hit.time,
                })

            conversations = Conversation.objects.filter(
                public_id__in=conversation_data.keys(),
                user=self.request.user
            )

            # Combine results
            results = []
            for conv in conversations:
                results.append({
                    "conversation_id": conv.public_id,
                    "title": conv.title,
                    "matching_prompts": conversation_data[str(conv.public_id)]["matching_prompts"]
                })

            return results

        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            raise ValidationError({"error": "Search operation failed"})


@extend_schema(operation_id="search_prompts_in_conversation")
class PromptsSearchView(generics.ListAPIView):
    """
    GET /api/conversations/{conversation_id}/prompts/search/?q=...
    returns list of { prompt_id }
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PromptSearchSerializer


    def get_queryset(self):
        try:
            conv = get_object_or_404(
                Conversation,
                public_id=self.kwargs["conversation_id"],
                user=self.request.user
            )
            q = self.request.GET.get("q", "").strip()
            if not q:
                raise ValidationError({"q": "Please provide a 'q' query parameter."})

            s = (
                PromptDocument.search()
                .query("multi_match", query=q, fields=["user_prompt", "response"])
                .filter("term", conversation_public_id=str(conv.public_id))
                .filter("term", user_id=self.request.user.id)
                .source(["public_id"])
            )
            response = s.execute()
            if not response.hits:
                return []
            return [{"prompt_id": hit.public_id} for hit in response.hits]
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            raise ValidationError({"error": "Search operation failed"})

# class ConversationSearchView(generics.GenericAPIView):
#     """
#     Search across a user's conversations by matching prompts/responses in Elasticsearch,
#     """
#     permission_classes = [IsAuthenticated]
#     serializer_class = ConversationSearchSerializer

#     def get(self, request, format=None):
#         q = request.GET.get('q', "")
#         if not q:
#             return Response({"detail": "Query parameter 'q' is required."},
#                             status=status.HTTP_400_BAD_REQUEST)

#         s = PromptDocument.search().query(
#             "multi_match",
#             query=q,
#             fields=["user_prompt", "response"]
#         )
#         s = s.filter("term", user_id=request.user.id)
#         s = s.source(["conversation_public_id"])

#         resp = s.execute()

#         conv_ids = {hit.conversation_public_id for hit in resp.hits}

#         conv_results = []

#         conversations = Conversation.objects.filter(
#             public_id__in=conv_ids,
#             user=request.user
#         ).prefetch_related("prompts")

#         for conv in conversations:
#             # find only the prompts in this conversation that match q
#             matching_prompts = [
#                 {
#                     "prompt_id": str(p.public_id),
#                     "user_prompt": p.user_prompt,
#                     "response":    p.response,
#                     "time":        p.time,
#                 }
#                 for p in conv.prompts.filter(
#                     models.Q(user_prompt__icontains=q)
#                     | models.Q(response__icontains=q)
#                 )
#             ]
#             conv_results.append({
#                 "conversation_id": str(conv.public_id),
#                 "title":           conv.title,
#                 "matching_prompts": matching_prompts,
#             })
#         serializer = self.get_serializer(conv_results, many=True)
#         return Response(serializer.data, status=status.HTTP_200_OK)


# class PromptsSearchView(generics.GenericAPIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request, conversation_id, format=None):
#         try:
#             conversation = Conversation.objects.get(
#                 public_id=conversation_id,
#                 user=request.user
#             )
#         except Conversation.DoesNotExist:
#             return Response({"error": "Conversation not found."},
#                             status=status.HTTP_404_NOT_FOUND)

#         q = request.GET.get("q", "")
#         if not q:
#             return Response({"error": "Please provide a 'q' query parameter."},
#                             status=status.HTTP_400_BAD_REQUEST)

#         s = PromptDocument.search().query(
#             "multi_match",
#             query=q,
#             fields=["user_prompt", "response"]
#         )
#         s = s.filter("term", conversation_public_id=str(conversation.public_id))

#         s = s.filter("term", user_id=request.user.id)

#         resp = s.execute()

#         prompts = [
#             {"prompt_id": hit.public_id}
#             for hit in resp.hits
#         ]

#         return Response(prompts)


class LLMModelListView(generics.ListAPIView):
    queryset = LLMModel.objects.all()
    serializer_class = LLMModelSerializer
    permission_classes = [IsAuthenticated]
