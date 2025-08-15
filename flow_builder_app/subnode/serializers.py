from typing import Any, Dict, List, Optional
from django.db.models import Q
from rest_framework import serializers
from flow_builder_app.subnode.models import SubNode
from flow_builder_app.parameter.models import ParameterValue
from flow_builder_app.node.models import NodeFamily,NodeVersion  # Import the related model

# ---------- SubNode Version Serializer ----------

class SubNodeVersionSerializer(serializers.ModelSerializer):
    updated_by = serializers.SerializerMethodField()
    parameter_values = serializers.SerializerMethodField()

    class Meta:
        model = SubNode
        fields = [
            'id', 'version', 'is_deployed', 'is_editable',
            'updated_at', 'updated_by', 'version_comment', 'parameter_values'
        ]
        read_only_fields = fields

    def get_updated_by(self, obj) -> str:
        return getattr(obj, 'updated_by', '')

    def get_parameter_values(self, obj) -> List[Dict[str, Any]]:
        pv_qs = getattr(obj, 'parameter_values', []) or []
        return [
            {
                "id": str(pv.id),
                "parameter_key": getattr(pv.parameter, 'key', '') if hasattr(pv, 'parameter') else '',
                "value": pv.value
            }
            for pv in pv_qs
        ]


# ---------- SubNode Serializer ----------

class SubNodeSerializer(serializers.ModelSerializer):
    versions = serializers.SerializerMethodField()
    active_version = serializers.SerializerMethodField()
    original_version = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    node_family = serializers.SerializerMethodField()
    all_version_parameters = serializers.SerializerMethodField()
    family_name = serializers.CharField(source='node_family.name', read_only=True)
    family_id = serializers.UUIDField(source='node_family.id', read_only=True)
    
    class Meta:
        model = SubNode
        fields = [
            'id', 'name', 'description', 'node_family',
            'active_version', 'original_version',
            'version_comment',
            'created_at', 'updated_at',
            'created_by', 'updated_by',
            'versions',"all_version_parameters"
        ]
        read_only_fields = fields

    def get_versions(self, obj):
        return SubNodeVersionSerializer(obj.get_all_versions(), many=True).data
    
    def get_all_version_parameters(self, obj):
        versions = obj.node_family.versions.order_by('-version')
        return SubNodeVersionParametersSerializer(
            versions,
            many=True,
            context={'subnode': obj}
        ).data
    
    def get_node_family(self, obj):
        if obj.node_family:
            return {"id": str(obj.node_family.id), "name": obj.node_family.name}
        return None
    
    def get_active_version(self, obj) -> Optional[int]:
        original_ref = getattr(obj, 'original', None) or obj
        active = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id),
            is_deployed=True
        ).order_by('-version').first()
        return active.version if active else None

    def get_original_version(self, obj) -> Optional[int]:
        original_ref = getattr(obj, 'original', None) or obj
        return getattr(original_ref, 'version', None)

    def get_versions(self, obj) -> List[Dict[str, Any]]:
        original_ref = getattr(obj, 'original', None) or obj
        versions_qs = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id)
        ).order_by('version')
        return SubNodeVersionSerializer(versions_qs, many=True, context=self.context).data

    def get_created_by(self, obj) -> str:
        return getattr(obj, 'created_by', '')

    def get_updated_by(self, obj) -> str:
        return getattr(obj, 'updated_by', '')


# ---------- Parameter Value Update Serializer ----------
class VersionSerializer(serializers.ModelSerializer):
    subnodes = serializers.SerializerMethodField()

    class Meta:
        model = NodeVersion
        fields = [
            'id',
            'version',
            'state',
            'changelog',
            'script_url',
            'subnodes',
            'created_at',
            'created_by',
        ]

    def get_subnodes(self, obj):
        # Filter subnodes for THIS version only
        subnodes_qs = SubNode.objects.filter(node_family=obj.family, version=obj.version)
        return SubNodeSerializer(subnodes_qs, many=True).data
class ParameterValueUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterValue
        fields = ['id', 'value']
        read_only_fields = ['id']


# ---------- SubNode Create Serializer ----------

class SubNodeCreateSerializer(serializers.ModelSerializer):
    node_family = serializers.PrimaryKeyRelatedField(
        queryset=NodeFamily.objects.all()
    )

    class Meta:
        model = SubNode
        fields = ['name', 'description', 'node_family']
        extra_kwargs = {
            'name': {'required': True},
            'description': {'required': True},
            'node_family': {'required': True},
        }


# subnode/serializers.py
class SubNodeVersionParametersSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    parameters = serializers.SerializerMethodField()

    def get_parameters(self, obj):
        subnode = self.context['subnode']
        return subnode.get_version_parameters(obj)