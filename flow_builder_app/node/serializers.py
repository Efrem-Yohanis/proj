from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from django.db.models import Prefetch
from rest_framework import serializers
from flow_builder_app.node.models import (
    NodeFamily, 
    NodeVersion, 
    NodeParameter, 
    NodeExecution, 
    NodeVersionLink
)
from flow_builder_app.parameter.models import Parameter,ParameterValue


class NodeFamilySerializer(serializers.ModelSerializer):
    """
    Serializer for NodeFamily with version information
    Includes latest and published version details
    """
    is_deployed = serializers.BooleanField(read_only=True)
    # latest_version = serializers.SerializerMethodField()
    published_version = serializers.SerializerMethodField()
    versions = serializers.SerializerMethodField()
   

    class Meta:
        model = NodeFamily
        fields = [
            "id", "name", "description", "created_at", "updated_at",
            "created_by", "is_deployed", "published_version", 
            "versions"
        ]
        read_only_fields = [
            "created_at", "updated_at", "is_deployed", 
            "published_version", "versions"
        ]
    
    def get_versions(self, obj):
        versions_qs = obj.versions.all().order_by('-version')
        return NodeVersionSerializer(versions_qs, many=True).data


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.context.get('request') and self.context['request'].method == 'GET':
            self.Meta.queryset = self.Meta.model.objects.prefetch_related(
                Prefetch('versions', queryset=NodeVersion.objects.order_by('-version'))
            )

    def validate_name(self, value: str) -> str:
        """Validate that name isn't changed if versions exist"""
        if self.instance and self.instance.versions.exists() and value != self.instance.name:
            raise serializers.ValidationError(
                "Cannot rename a family with existing versions."
            )
        return value

    def get_latest_version(self, obj: NodeFamily) -> Optional[Dict[str, Any]]:
        latest = obj.versions.order_by("-version").first()
        if latest:
            return NodeVersionSerializer(latest, context=self.context).data
        return None

    def get_published_version(self, obj: NodeFamily) -> Optional[Dict[str, Any]]:
        published = obj.versions.filter(state="published").order_by("-version").first()
        if published:
            return NodeVersionSerializer(published, context=self.context).data
        return None

    def get_subnodes_count(self, obj: NodeFamily) -> int:
        return obj.subnodes.count()

    def get_subnodes(self, obj):
        """Get basic subnode information at family level"""
        return [
            {
                "id": str(child.id),
                "name": child.name,
                "description": child.description,
                "latest_version": {
                    "version": child.versions.order_by('-version').first().version,
                    "state": child.versions.order_by('-version').first().state
                } if child.versions.exists() else None
            }
            for child in obj.child_nodes.all()
        ]
    

class NodeVersionLinkSerializer(serializers.ModelSerializer):
    """
    Serializer for version-to-version relationships
    """
    child_version = serializers.SerializerMethodField()
    parent_version_id = serializers.UUIDField(source='parent_version.id', read_only=True)

    class Meta:
        model = NodeVersionLink
        fields = ['id', 'order', 'parent_version_id', 'child_version']
        read_only_fields = fields

    def get_child_version(self, obj: NodeVersionLink) -> Dict[str, Any]:
        """Get serialized child version data"""
        from .serializers import NodeVersionSerializer  # Local import to avoid circularity
        return NodeVersionSerializer(
            obj.child_version, 
            context=self.context
        ).data

class NodeVersionSerializer(serializers.ModelSerializer):
    script_url = serializers.SerializerMethodField()
    parameters = serializers.SerializerMethodField()
    subnodes = serializers.SerializerMethodField()
    family_name = serializers.CharField(source='family.name', read_only=True)

    class Meta:
        model = NodeVersion
        fields = [
            "id", "version", "state", "changelog", "family", "family_name",
            "script_url", "parameters", "subnodes",
            "created_at", "created_by"
        ]
        read_only_fields = fields

    def get_family_parameters(self, obj):
        """Get parameters available to all versions in this family"""
        # Get all unique parameters ever used in this family's versions
        parameter_ids = NodeParameter.objects.filter(
            node_version__family=obj.family
        ).values_list('parameter_id', flat=True).distinct()

        # Get the parameter objects
        parameters = Parameter.objects.filter(id__in=parameter_ids)

        return [
            {
                "id": str(param.id),
                "key": param.key,
                "datatype": getattr(param, 'datatype', None),
                "default_value": param.default_value,
                "is_active": param.is_active
            }
            for param in parameters
        ]

    def get_parameters(self, obj):
        """Get parameters specific to this version"""
        parameters = obj.nodeparameter_set.select_related('parameter').all()
        return [
            {
                "id": str(np.id),
                "parameter_id": str(np.parameter.id),
                "key": np.parameter.key,
                "value": np.value,
                "datatype": getattr(np.parameter, 'datatype', None)
            }
            for np in parameters
        ]

    def get_script_url(self, obj):
        request = self.context.get("request")
        if obj.script and request:
            return request.build_absolute_uri(obj.script.url)
        return None

    def get_subnodes(self, obj):
        """Get all linked subnode families for this version"""
        links = obj.child_links.select_related('child_version__family').all()
        
        return [
            {
                "link_id": str(link.id),
                "order": link.order,
                "family": {
                    "id": str(link.child_version.family.id),
                    "name": link.child_version.family.name,
                    "is_deployed": link.child_version.family.is_deployed
                },
                "version": {
                    "id": str(link.child_version.id),
                    "version": link.child_version.version,
                    "state": link.child_version.state,
                    "parameters": [
                        {
                            "key": np.parameter.key,
                            "value": np.value,
                            "datatype": getattr(np.parameter, 'datatype', None)
                        }
                        for np in getattr(link.child_version, 'nodeparameter_set', []).all()
                    ]
                }
            }
            for link in links
        ]
    
class NodeExecutionSerializer(serializers.ModelSerializer):
    """
    Serializer for NodeExecution records with detailed version info
    """
    duration = serializers.SerializerMethodField()
    version_details = serializers.SerializerMethodField()

    class Meta:
        model = NodeExecution
        fields = [
            "id", "version_details", "status",
            "started_at", "completed_at", "duration",
            "triggered_by", "log", "artifacts"
        ]
        read_only_fields = fields

    def get_duration(self, obj: NodeExecution) -> Optional[float]:
        """Calculate execution duration in seconds"""
        return obj.duration

    def get_version_details(self, obj: NodeExecution) -> Dict[str, Any]:
        """Get version details without full serialization"""
        return {
            "id": str(obj.version.id),
            "version": obj.version.version,
            "state": obj.version.state,
            "family_name": obj.version.family.name
        }

class NodeFamilyExportSerializer(serializers.ModelSerializer):
    """
    Serializer for exporting NodeFamily with all versions
    """
    versions = serializers.SerializerMethodField()
    subnodes = serializers.SerializerMethodField()

    class Meta:
        model = NodeFamily
        fields = ["id", "name", "description", "versions", "subnodes"]

    def get_versions(self, obj: NodeFamily) -> List[Dict[str, Any]]:
        """Get all versions with parameters"""
        return NodeVersionSerializer(
            obj.versions.all(),
            many=True,
            context=self.context
        ).data

    def get_subnodes(self, obj: NodeFamily) -> List[Dict[str, Any]]:
        """Get basic subnode information"""
        return [
            {
                "id": str(subnode.id),
                "name": subnode.name
            }
            for subnode in obj.subnodes.all()
        ]

class NodeFamilyImportSerializer(serializers.Serializer):
    """
    Serializer for importing NodeFamily data
    """
    file = serializers.FileField()

    def validate_file(self, file) -> Dict[str, Any]:
        """Validate and parse the import file"""
        try:
            file.seek(0)
            data = json.load(file)
        except json.JSONDecodeError:
            raise serializers.ValidationError("Invalid JSON file.")
        
        if not isinstance(data, dict):
            raise serializers.ValidationError("Root of JSON must be an object.")
        
        required_fields = ["name", "versions"]
        for field in required_fields:
            if field not in data:
                raise serializers.ValidationError(f"Missing required field: {field}")
            
        if not isinstance(data["versions"], list):
            raise serializers.ValidationError("'versions' must be a list")
            
        return data

class NodeVersionCreateSerializer(serializers.ModelSerializer):
    """
    Specialized serializer for creating new versions with optional source version copying
    """
    source_version = serializers.IntegerField(
        required=False, 
        allow_null=True,
        help_text="Version number to copy from (optional)"
    )

    class Meta:
        model = NodeVersion
        fields = ["changelog", "source_version"]
        extra_kwargs = {
            "changelog": {"required": True, "allow_blank": False}
        }

    def validate_source_version(self, value):
        """Validate that source version exists in the same family"""
        if value is None:
            return value
            
        family_id = self.context.get('family_id')
        if not NodeVersion.objects.filter(
            family_id=family_id, 
            version=value
        ).exists():
            raise serializers.ValidationError(
                "Source version does not exist in this family"
            )
        return value
class ParameterUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating multiple parameters at once
    """
    def to_internal_value(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate parameter update payload structure"""
        if not isinstance(data, dict):
            raise serializers.ValidationError("Payload must be a JSON object")
        
        errors = {}
        for key, value in data.items():
            if not isinstance(key, str) or len(key) < 8:
                errors[key] = "Invalid parameter ID format"
            if not isinstance(value, (str, int, float, bool, list, dict, type(None))):
                errors[key] = "Invalid parameter value type"
                
        if errors:
            raise serializers.ValidationError(errors)
            
        return data

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that parameters exist"""
        param_ids = attrs.keys()
        existing_params = set(Parameter.objects.filter(
            id__in=param_ids
        ).values_list('id', flat=True))
        
        missing = set(param_ids) - existing_params
        if missing:
            raise serializers.ValidationError(
                f"Parameters not found: {', '.join(missing)}"
            )
            
        return attrs

class ScriptUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating node script files
    """
    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    script = serializers.FileField(required=True)

    def validate_script(self, value) -> Any:
        """Validate script file properties"""
        if value.size > self.MAX_SIZE:
            raise serializers.ValidationError(
                f"Script too large. Max size is {self.MAX_SIZE/1024/1024}MB"
            )
            
        name = getattr(value, "name", "")
        if not name.lower().endswith(".py"):
            raise serializers.ValidationError("Only Python (.py) files allowed")
            
        # Optional: Add basic Python syntax validation
        try:
            content = value.read().decode('utf-8')
            compile(content, name, 'exec')
            value.seek(0)  # Reset file pointer
        except SyntaxError as e:
            raise serializers.ValidationError(f"Invalid Python syntax: {str(e)}")
            
        return value
    
class SubNodeFamilySerializer(serializers.ModelSerializer):
    class Meta:
        model = NodeFamily
        fields = ['id', 'name', 'description', 'is_deployed']

class SubnodeVersionParameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterValue
        fields = ["key", "value", "datatype"]

class SubnodeVersionSerializer(serializers.ModelSerializer):
    parameters = SubnodeVersionParameterSerializer(source="parametervalues", many=True)

    class Meta:
        model = NodeVersion
        fields = ["id", "parameters"]

class SubnodeSerializer(serializers.ModelSerializer):
    subnode_name = serializers.CharField(source="child_version.family.name", read_only=True)
    id = serializers.UUIDField(source="child_version.family.id", read_only=True)
    parameters = serializers.SerializerMethodField()

    class Meta:
        model = NodeVersionLink
        fields = ["subnode_name", "id", "parameters"]

    def get_parameters(self, obj):
        # Return parameters of the child version only
        return SubnodeVersionParameterSerializer(obj.child_version.parametervalues.all(), many=True).data

class NodeVersionDetailSerializer(serializers.ModelSerializer):
    parameters = SubnodeVersionParameterSerializer(source="parametervalues", many=True)
    subnodes = serializers.SerializerMethodField()

    class Meta:
        model = NodeVersion
        fields = [
            "id", "version", "state", "changelog", "family", "family_name",
            "script_url", "parameters", "subnodes", "created_at", "created_by"
        ]

    def get_subnodes(self, obj):
        # Exclude self-links
        links = NodeVersionLink.objects.filter(parent_version=obj).exclude(
            child_version__family=obj.family
        )
        return SubnodeSerializer(links, many=True).data
