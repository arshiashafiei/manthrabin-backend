from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse
from django.db import connections

from .models import User, Interest, UserInterest, PasswordReset
from django.contrib.auth.tokens import PasswordResetTokenGenerator

from .permissions import IsAdminUserType
from .serializers import \
    HomeSerializer, LoginInputSerializer, \
    RegisterSerializer, UserSerializer, \
    InterestSerializer, UserProfileUpdateSerializer, \
    PasswordChangeSerializer, ResetPasswordRequestSerializer, \
    ResetPasswordSerializer, UpdateAccountTypeSerializer


class RegisterView(APIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': UserSerializer(user).data,
                'redirect_to': '/select-interests'
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    serializer_class = LoginInputSerializer
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        user = User.objects.filter(email=email).first()

        if user and user.check_password(password):
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': UserSerializer(user).data
            })
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)


class HomeView(APIView):
    serializer_class = HomeSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = self.serializer_class(request.user)
        return Response(serializer.data)


class UsersListView(APIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated,IsAdminUserType]

    def get(self, request):
        users = User.objects.all()
        serializer = self.serializer_class(users, many=True)
        return Response(serializer.data)

class UserBlockView(APIView):
    permission_classes = [IsAuthenticated,IsAdminUserType]
    def get(self, request, **kwargs):
        user = get_object_or_404(User, PublicID=kwargs['user_id'])
        user.IsActive = not user.IsActive
        user.is_active =  user.IsActive
        user.save()
        return Response({
            'message': f"User {'deactivated' if not user.is_active else 'reactivated'} successfully",
            'user_id': str(user.PublicID),
            'is_active': user.IsActive
        })

class InterestListView(APIView):
    serializer_class = InterestSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        interests = Interest.objects.all()
        serializer = self.serializer_class(interests, many=True)
        return Response(serializer.data)


class SaveUserInterestsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        selected_interest_ids = request.data.get("interests", [])

        # Delete existing interests
        UserInterest.objects.filter(UserID=user).delete()

        # Add new interests
        for interest_id in selected_interest_ids:
            interest = Interest.objects.get(InterestID=interest_id)
            UserInterest.objects.create(UserID=user, InterestID=interest)

        return Response({"message": "Interests saved successfully."}, status=status.HTTP_200_OK)


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserSerializer
        return UserProfileUpdateSerializer

    def get(self, request):
        serializer = self.get_serializer_class()(request.user)
        return Response(serializer.data)

    def put(self, request):
        serializer = self.get_serializer_class()(
            request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    serializer_class = PasswordChangeSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            user = request.user
            current_password = serializer.validated_data.get(
                'current_password')
            new_password = serializer.validated_data.get('new_password')

            # verify current password
            if not user.check_password(current_password):
                return Response({'error': 'Current password is incorrect'}, status=status.HTTP_400_BAD_REQUEST)

            # Set new password
            user.set_password(new_password)
            user.save()

            # Generate new token
            refresh = RefreshToken.for_user(user)
            return Response({
                'message': 'Password changed successfully',
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RequestResetPasswordView(APIView):
    permission_classes = [AllowAny]
    serializer_class = ResetPasswordRequestSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        email = request.data['email']
        user = User.objects.filter(email__iexact=email).first()
        if user:
            token_generator = PasswordResetTokenGenerator()
            token = token_generator.make_token(user)
            reset = PasswordReset(email=email, token=token)
            reset.save()
            reset_url = f"manthrabin.ir/reset-password/{token}"

            send_mail(
                subject="Password Reset Request",
                message=f"Click the link below to reset your password:\n{reset_url}",
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[email],
                fail_silently=False,
            )

            return Response({'success': 'We have sent you a link to reset your password'}, status=status.HTTP_200_OK)
        else:
            return Response({"error": "User with credentials not found"}, status=status.HTTP_404_NOT_FOUND)


class ResetPasswordView(APIView):
    serializer_class = ResetPasswordSerializer
    permission_classes = []

    def post(self, request, token):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        reset_obj = PasswordReset.objects.filter(token=token).first()

        if not reset_obj:
            return Response({'error': 'Invalid token'}, status=400)

        user = User.objects.filter(email=reset_obj.email).first()

        if user:
            user.set_password(request.data['new_password'])
            user.save()
            reset_obj.delete()

            return Response({'message': 'Password updated successfully'})
        else:
            return Response({'error': 'No user found'}, status=404)


class UpdateAccountTypeView(APIView):
    serializer_class = UpdateAccountTypeSerializer
    permission_classes = [IsAuthenticated,IsAdminUserType]

    def put(self, request, pk):  # pk for user's PublicID
        try:
            user = User.objects.get(PublicID=pk)
        except User.DoesNotExist:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(user, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(UserSerializer(user).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def HealthCheckView(request):
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute("SELECT 1;")
    except Exception as e:
        return JsonResponse(
            {"status": "fail", "error": str(e)},
            status=500
        )

    return JsonResponse({"status": "ok"})
