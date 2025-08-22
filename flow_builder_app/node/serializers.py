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
from flow_builder_app.subnode.serializers import SubNodeVersionSerializer
from flow_builder_app.subnode.models import SubNode, SubNodeParameterValue


class NodeVersionSerializer(serializers.ModelSerializer):
    family_name = serializers.CharField(source='family.name', read_only=True)
    parameters = serializers.SerializerMethodField()
    subnodes = serializers.SerializerMethodField()
    script_url = serializers.SerializerMethodField()

    class Meta:
        model = NodeVersion
        fields = [
            "id",
            "version",
            "state",
            "changelog",
            "family",
            "family_name",
            "script_url",
            "parameters",
            "subnodes",
            "created_at",
            "created_by"
        ]
        read_only_fields = fields

    def get_parameters(self, obj):
        # Use the related_name='parameters' from NodeParameter
        return [
            {
                "id": str(np.id),
                "parameter_id": str(np.parameter.id),
                "key": np.parameter.key,
                "value": np.value,
                "datatype": getattr(np.parameter, 'datatype', None)
            }
            for np in obj.parameters.select_related('parameter').all()
        ]

    def get_script_url(self, obj):
        request = self.context.get("request")
        if obj.script and request:
            return request.build_absolute_uri(obj.script.url)
        return None

    def get_subnodes(self, obj):
        """Subnodes with active version parameters - replicate family endpoint logic"""
        links = obj.child_links.select_related("child_version__family").all()
        result = []
        processed_subnodes = set()
        
        for link in links:
            child_family = link.child_version.family
            
            # Get ALL subnodes for this family (not just active ones)
            subnodes = SubNode.objects.filter(node_family=child_family)
            
            # Create a mapping of subnode name to active version
            active_subnodes_map = {}
            for subnode in subnodes:
                if subnode.is_deployed:
                    active_subnodes_map[subnode.name] = subnode
            
            for name, active_subnode in active_subnodes_map.items():
                # Skip if we already processed this subnode
                if str(active_subnode.id) in processed_subnodes:
                    continue
                    
                processed_subnodes.add(str(active_subnode.id))
                
                # Use the same logic as the family endpoint
                param_values = {}
                
                # Get all ParameterValue objects for this active subnode
                param_values_queryset = SubNodeParameterValue.objects.filter(
                    subnode=active_subnode
                ).select_related('parameter')
                
                # Create a dictionary of parameter key -> value
                for pv in param_values_queryset:
                    param_values[pv.parameter.key] = pv.value
                
                # Also include parameters from the node version that might not have subnode-specific values
                for np in obj.parameters.select_related('parameter').all():
                    if np.parameter.key not in param_values:
                        param_values[np.parameter.key] = np.value or np.parameter.default_value
                
                result.append({
                    "id": str(active_subnode.id),
                    "name": active_subnode.name,
                    "active_version": active_subnode.version,
                    "parameter_values": param_values
                })
        
        return result

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
                "created_by", "is_deployed", "published_version", "versions"
            ]

    # def get_versions(self, obj):
    #     """
    #     For each node version, return parameters and for each subnode the value
    #     taken from NodeParameter (version-level). Falls back to Parameter.default_value.
    #     """
    #     node_versions = NodeVersion.objects.filter(family=obj).order_by("version")

    #     # fetch subnodes once
    #     subnodes = list(SubNode.objects.filter(node_family=obj))

    #     results = []
    #     for nv in node_versions:
    #         # NodeParameter objects for this version
    #         node_params = NodeParameter.objects.filter(node_version=nv).select_related('parameter')

    #         # parameters list for response
    #         params = [
    #             {
    #                 "id": str(np.parameter.id),
    #                 "key": np.parameter.key,
    #                 "datatype": np.parameter.datatype,
    #                 "default_value": np.parameter.default_value
    #             }
    #             for np in node_params
    #         ]

    #         # build map parameter_id -> value for this version
    #         param_value_map = { str(np.parameter.id): np.value for np in node_params }

    #         # For each subnode, apply the version-level value
    #         subnodes_list = []
    #         for sn in subnodes:
    #             param_values = {}
    #             for np in node_params:
    #                 pid = str(np.parameter.id)
    #                 value = param_value_map.get(pid)
    #                 if value is None:
    #                     value = np.parameter.default_value
    #                 param_values[np.parameter.key] = value

    #             subnodes_list.append({
    #                 "id": str(sn.id),
    #                 "name": sn.name,
    #                 "parameter_values": param_values
    #             })

    #         results.append({
    #             "version": nv.version,
    #             "parameters": params,
    #             "subnodes": subnodes_list
    #         })
    #     return results

    def get_versions(self, obj):
        """For each node version, return parameters and subnodes with ACTIVE versions"""
        node_versions = NodeVersion.objects.filter(family=obj).order_by("version")
        results = []
        
        # Get all subnodes for this family once
        all_subnodes = SubNode.objects.filter(node_family=obj)
        
        # Create a mapping of subnode name to active version
        active_subnodes_map = {}
        for subnode in all_subnodes:
            if subnode.is_deployed:
                active_subnodes_map[subnode.name] = subnode
        
        for nv in node_versions:
            # NodeParameter objects for this version
            node_params = NodeParameter.objects.filter(node_version=nv).select_related('parameter')

            # parameters list for response
            params = [
                {
                    "id": str(np.parameter.id),
                    "key": np.parameter.key,
                    "datatype": np.parameter.datatype,
                    "default_value": np.parameter.default_value
                }
                for np in node_params
            ]

            # For each active subnode, get parameters
            subnodes_list = []
            
            for name, active_subnode in active_subnodes_map.items():
                # Get parameters for the active version using the correct method
                param_values = {}
                
                # Get all ParameterValue objects for this active subnode
                param_values_queryset = SubNodeParameterValue.objects.filter(
                    subnode=active_subnode
                ).select_related('parameter')
                
                # Create a dictionary of parameter key -> value
                for pv in param_values_queryset:
                    param_values[pv.parameter.key] = pv.value
                
                # Also include parameters from the node version that might not have subnode-specific values
                for np in node_params:
                    if np.parameter.key not in param_values:
                        param_values[np.parameter.key] = np.value or np.parameter.default_value
                
                subnodes_list.append({
                    "id": str(active_subnode.id),
                    "name": active_subnode.name,
                    "active_version": active_subnode.version,
                    "parameter_values": param_values
                })
            
            results.append({
                "version": nv.version,
                "parameters": params,
                "subnodes": subnodes_list
            })
        return results
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
    
    def get_published_version(self, obj):
        """Get the latest published version only"""
        published = obj.versions.filter(state="published").order_by("-version").first()
        if published:
            return NodeVersionSerializer(published, context=self.context).data
        return None

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
    Accepts either a FileField named 'script' or a text field 'script_text'.
    """
    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    script = serializers.FileField(required=False)
    script_text = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs) -> Any:
        # Validate that at least one input is provided
        script_file = attrs.get('script')
        script_text = attrs.get('script_text')

        if not script_file and not script_text:
            raise serializers.ValidationError("Provide either 'script' (file) or 'script_text' (string).")

        # If file provided, validate file properties and python syntax
        if script_file:
            if hasattr(script_file, 'size') and script_file.size > self.MAX_SIZE:
                raise serializers.ValidationError(
                    f"Script too large. Max size is {self.MAX_SIZE/1024/1024}MB"
                )
            name = getattr(script_file, "name", "")
            if not name.lower().endswith(".py"):
                raise serializers.ValidationError("Only Python (.py) files allowed")

            try:
                content = script_file.read()
                # content may be bytes
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                compile(content, name or '<uploaded_script>', 'exec')
            except SyntaxError as e:
                raise serializers.ValidationError(f"Invalid Python syntax: {str(e)}")
            finally:
                try:
                    script_file.seek(0)
                except Exception:
                    pass

        # If text provided, validate Python syntax
        if script_text:
            try:
                compile(script_text, '<script_text>', 'exec')
            except SyntaxError as e:
                raise serializers.ValidationError(f"Invalid Python syntax in script_text: {str(e)}")

        return attrs
    
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



class FamilyParameterSerializer(serializers.Serializer):
    status = serializers.CharField()
    key = serializers.CharField()
    datatype = serializers.CharField(required=False)


class NodeFamilyVersionSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    parameters = serializers.ListField()
    subnodes = serializers.ListField()


class NodeExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NodeExecution
        fields = '__all__'
