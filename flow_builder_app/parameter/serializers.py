from rest_framework import serializers
from .models import Parameter, ParameterValue


# ---------- Parameter Serializer ----------

class ParameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parameter
        fields = [
            'id',
            'key',
            'default_value',
            'datatype',
            'is_active',
            'created_at',
            'created_by',
        ]
        extra_kwargs = {
            'key': {'required': True},
            'datatype': {'required': False, 'allow_null': True, 'allow_blank': True},
            'default_value': {'required': False, 'allow_blank': True},
        }


# ---------- ParameterValue Serializer ----------

class ParameterValueSerializer(serializers.ModelSerializer):
    parameter_key = serializers.CharField(source='parameter.key', read_only=True)

    class Meta:
        model = ParameterValue
        fields = [
            'id',
            'parameter',
            'parameter_key',
            'subnode',
            'value',
        ]
        extra_kwargs = {
            'parameter': {'required': True},
            'subnode': {'required': True},
            'value': {'required': True},
        }
