import logging
import json
from datetime import datetime
from django.http import JsonResponse
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from .models import Parameter, ParameterValue
from .serializers import ParameterSerializer, ParameterValueSerializer

logger = logging.getLogger(__name__)


class ParameterViewSet(viewsets.ModelViewSet):
    queryset = Parameter.objects.all()
    serializer_class = ParameterSerializer
    permission_classes = [AllowAny]

    def get_permissions(self):
        # Allow any user to access deploy/undeploy/clone/import endpoints
        if self.action in ['deploy', 'undeploy', 'clone', 'import_json']:
            return [AllowAny()]
        if self.request.method in ['PATCH', 'DELETE']:
            return [AllowAny()]
        return super().get_permissions()

    def perform_create(self, serializer):
        self._validate_default_value(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_default_value(serializer.validated_data)
        serializer.save()

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_active:
            return Response(
                {"detail": "Cannot edit a deployed parameter. Undeploy first."},
                status=status.HTTP_403_FORBIDDEN
            )
        allowed_fields = ['key', 'default_value', 'datatype', 'is_active']
        data = {k: v for k, v in request.data.items() if k in allowed_fields}
        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    def _validate_default_value(self, data):
        default_value = data.get('default_value')
        datatype = data.get('datatype')
        if default_value is None or datatype is None:
            return
        try:
            if datatype == 'integer':
                int(default_value)
            elif datatype == 'float':
                float(default_value)
            elif datatype == 'boolean':
                if str(default_value).lower() not in ['true', 'false', '1', '0']:
                    raise ValueError()
            elif datatype == 'json':
                json.loads(default_value)
            elif datatype == 'date':
                datetime.strptime(default_value, '%Y-%m-%d')
            elif datatype == 'datetime':
                datetime.fromisoformat(default_value)
        except Exception:
            logger.error(f"Default value validation failed: '{default_value}' for type '{datatype}'")
            raise ValidationError(
                f"Default value '{default_value}' does not match datatype '{datatype}'."
            )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_active:
            return Response(
                {"detail": "Cannot delete an active/deployed parameter. Undeploy it first."},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)
    # ----------- Custom Actions -----------
  
    @action(detail=True, methods=['post'])
    def deploy(self, request, pk=None):
        param = self.get_object()
        if param.is_active:
            return Response({"detail": "Parameter is already deployed."}, status=status.HTTP_200_OK)
        param.is_active = True
        param.save()
        return Response(self.get_serializer(param).data)

    @action(detail=True, methods=['post'])
    def undeploy(self, request, pk=None):
        param = self.get_object()
        if not param.is_active:
            return Response({"detail": "Parameter is already undeployed."}, status=status.HTTP_200_OK)
        param.is_active = False
        param.save()
        return Response(self.get_serializer(param).data)

    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        param = self.get_object()
        serializer = self.get_serializer(param)
        response = JsonResponse(serializer.data)
        response['Content-Disposition'] = f'attachment; filename=parameter_{param.id}.json'
        return response

    @action(detail=False, methods=['post'], parser_classes=[JSONParser])
    def import_json(self, request):
        content_type = request.content_type or ''
        if not content_type.startswith('application/json'):
            return Response(
                {"detail": "Unsupported Media Type. Use application/json."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        original = self.get_object()
        data = ParameterSerializer(original).data
        data.pop('id', None)
        base_key = f"{data['key']}_copy"
        new_key = base_key
        i = 1
        while Parameter.objects.filter(key=new_key).exists():
            new_key = f"{base_key}{i}"
            i += 1
        data['key'] = new_key
        data['is_active'] = False
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        clone = serializer.save()
        return Response(self.get_serializer(clone).data, status=status.HTTP_201_CREATED)


class ParameterValueViewSet(viewsets.ModelViewSet):
    """
    API endpoint for CRUD operations on ParameterValue
    """
    queryset = ParameterValue.objects.all()
    serializer_class = ParameterValueSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        created_by = getattr(user, 'username', 'anonymous')
        serializer.save()

    def perform_update(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        last_updated_by = getattr(user, 'username', 'anonymous')
        serializer.save()
