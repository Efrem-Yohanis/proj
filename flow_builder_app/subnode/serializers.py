from typing import Any, Dict, List, Optional
from rest_framework import serializers
from django.db.models import Q

from flow_builder_app.subnode.models import SubNode
from flow_builder_app.parameter.models import ParameterValue
from flow_builder_app.node.models import NodeFamily, NodeVersion

from flow_builder_app.subnode.models import SubNodeParameterValue
# ---------------- SubNode Version Serializer ----------------
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
        """
        Load parameters for this SubNode version by matching NodeFamily version.
        """
        try:
            # Get the NodeFamily version that matches this SubNode version
            node_version = obj.node_family.versions.filter(version=obj.version).first()
            if not node_version:
                return []

            # Get all ParameterValues for this SubNode
            values = ParameterValue.objects.filter(subnode=obj)

            # Map values with parameter info
            return [
                {
                    "id": str(pv.id),
                    "key": pv.parameter.key,
                    "datatype": pv.parameter.datatype,
                    "value": pv.value or pv.parameter.default_value
                }
                for pv in values.select_related('parameter')
            ]
        except Exception:
            return []


# ---------------- SubNode Serializer ----------------
class SubNodeSerializer(serializers.ModelSerializer):
    versions = serializers.SerializerMethodField()
    active_version = serializers.SerializerMethodField()
    original_version = serializers.SerializerMethodField()
    node_family = serializers.SerializerMethodField()
    family_name = serializers.CharField(source='node_family.name', read_only=True)
    family_id = serializers.UUIDField(source='node_family.id', read_only=True)
    versions = serializers.SerializerMethodField()


    class Meta:
            model = SubNode
            fields = [
                "id", "name", "description", "node_family",
                "active_version", "original_version",
                "created_at", "created_by", "versions"
            ]

    def get_versions(self, obj):
        family = NodeFamily.objects.get(id=obj.node_family.id)
        node_versions = NodeVersion.objects.filter(family=family).order_by("version")

        results = []
        previous_params = {}

        for nv in node_versions:
            # All family-level parameters for this node version
            family_params = {p.key: p for p in nv.parameters.all()}

            # SubNode's values for this version
            param_values = {
                pv.parameter.key: pv.value
                for pv in ParameterValue.objects.filter(subnode=obj)
            }

            display_params = []
            for key, param in family_params.items():
                value = param_values.get(key, param.default_value)

                if nv.version == 1:
                    display_params.append({
                        "status": "ParameterValue",
                        "key": key,
                        "value": value,
                        "datatype": param.datatype
                    })
                else:
                    if key in previous_params:
                        if value == previous_params[key]:
                            status = "Keeps"
                        else:
                            status = "Overrides"
                        display_params.append({
                            "status": status,
                            "key": key,
                            "value": value,
                            "datatype": param.datatype
                        })
                    else:
                        display_params.append({
                            "status": "Adds",
                            "key": key,
                            "value": value,
                            "datatype": param.datatype
                        })

            previous_params = {p.key: param_values.get(p.key, p.default_value) for p in family_params.values()}

            results.append({
                "version": nv.version,
                "parameters": display_params
            })

        return SubNodeVersionSerializer(results, many=True).data

    def get_active_version(self, obj) -> Optional[int]:
        active = SubNode.objects.filter(
            node_family=obj.node_family,
            is_deployed=True
        ).order_by('-version').first()
        return active.version if active else None

    def get_original_version(self, obj) -> Optional[int]:
        return getattr(obj, 'version', None)

    def get_node_family(self, obj) -> Optional[Dict[str, Any]]:
        if obj.node_family:
            return {"id": str(obj.node_family.id), "name": obj.node_family.name}
        return None


# ---------------- SubNode Create Serializer ----------------
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


# ---------------- Parameter Value Update Serializer ----------------
class ParameterValueUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterValue
        fields = ['id', 'value']
        read_only_fields = ['id']


# ---------------- SubNode Version Parameters Serializer ----------------
class SubNodeVersionParametersSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    parameters = serializers.SerializerMethodField()

    def get_parameters(self, obj):
        subnode: SubNode = self.context['subnode']
        node_version = subnode.node_family.versions.filter(version=obj).first()
        if not node_version:
            return []

        return [
            {
                "id": str(pv.id),
                "key": pv.parameter.key,
                "datatype": pv.parameter.datatype,
                "value": pv.value or pv.parameter.default_value
            }
            for pv in SubNodeParameterValue.objects.filter(subnode=subnode).select_related('parameter')
        ]


class ParameterValueDisplaySerializer(serializers.Serializer):
    status = serializers.CharField()
    key = serializers.CharField()
    value = serializers.CharField(allow_null=True, required=False)
    datatype = serializers.CharField(required=False)