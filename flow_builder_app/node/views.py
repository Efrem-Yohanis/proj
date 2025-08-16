import logging
from django.db import transaction
from django.db.models import Max, Exists, OuterRef, Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from flow_builder_app.parameter.models import Parameter
from flow_builder_app.node.models import (
    NodeFamily, 
    NodeVersion, 
    NodeExecution,
    NodeParameter,
    NodeVersionLink
)
from flow_builder_app.node.serializers import (
    NodeFamilySerializer,
    NodeFamilyExportSerializer,
    NodeFamilyImportSerializer,
    NodeVersionSerializer,
    NodeVersionCreateSerializer,
    NodeExecutionSerializer,
    ParameterUpdateSerializer,
    ScriptUpdateSerializer,
    NodeVersionLinkSerializer
)

logger = logging.getLogger(__name__)


class NodeFamilyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for NodeFamily operations with version management
    """
    queryset = NodeFamily.objects.prefetch_related(
        Prefetch('versions', queryset=NodeVersion.objects.order_by('-version'))
    ).all()
    serializer_class = NodeFamilySerializer
    lookup_field = 'id'
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list':
            return queryset.annotate(
                deployed_flag=Exists(
                    NodeVersion.objects.filter(
                        family=OuterRef('pk'),
                        state='published'
                    )
                )
            )
        return queryset


    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)

        data = serializer.data
        total_count = len(data)

        # Count published and draft based on `is_deployed`
        published_count = sum(1 for nf in data if nf.get('is_deployed') is True)
        draft_count = sum(1 for nf in data if nf.get('is_deployed') is False)

        # Wrap the original list into an object with counts
        response_data = {
            "total": total_count,
            "published": published_count,
            "draft": draft_count,
            "results": data
        }
        return Response(response_data)

    @action(detail=True, methods=['POST'])
    def add_subnode(self, request, id=None):
        """Add a subnode to this node family"""
        family = self.get_object()
        subnode_id = request.data.get('subnode_id')
        
        if not subnode_id:
            logger.warning(f"Missing subnode_id in request: {request.data}")
            return Response(
                {"error": "subnode_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            subnode = NodeFamily.objects.get(id=subnode_id)
            if subnode == family:
                raise ValidationError("Cannot add a node as its own subnode")
        except NodeFamily.DoesNotExist:
            logger.error(f"Subnode not found: {subnode_id}")
            return Response(
                {"error": "Subnode not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        with transaction.atomic():
            family.child_nodes.add(subnode)
            logger.info(f"Added subnode {subnode_id} to family {family.id}")
            return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['GET'])
    def export(self, request, id=None):
        """Export node family with all versions"""
        family = self.get_object()
        serializer = NodeFamilyExportSerializer(family, context={'request': request})
        response = Response(serializer.data)
        response['Content-Disposition'] = (
            f'attachment; filename="{family.name}_export.json"'
        )
        return response

    @action(detail=True, methods=['POST'])
    def clone(self, request, id=None):
        """Clone an entire node family with all versions"""
        family = self.get_object()
        new_name = request.data.get('new_name')

        if not new_name:
            return Response(
                {"error": "new_name is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            try:
                new_family = NodeFamily.objects.create(
                    name=new_name,
                    description=family.description,
                    created_by=request.user.username
                )

                # Clone all versions
                for version in family.versions.all():
                    new_version = NodeVersion.objects.create(
                        family=new_family,
                        version=version.version,
                        state=version.state,
                        script=version.script,
                        changelog=f"Cloned from {family.name} v{version.version}",
                        created_by=request.user.username
                    )
                    # Clone parameters
                    NodeParameter.objects.bulk_create([
                        NodeParameter(
                            node_version=new_version,
                            parameter=np.parameter,
                            value=np.value
                        )
                        for np in version.nodeparameter_set.all()
                    ])
                    # Clone subnode links
                    NodeVersionLink.objects.bulk_create([
                        NodeVersionLink(
                            parent_version=new_version,
                            child_version=link.child_version,
                            order=link.order
                        )
                        for link in version.child_links.all()
                    ])

                return Response(
                    NodeFamilySerializer(new_family, context={'request': request}).data,
                    status=status.HTTP_201_CREATED
                )
            except Exception as e:
                logger.error(f"Error cloning family: {str(e)}")
                return Response(
                    {"error": "Failed to clone family"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

    @action(detail=False, methods=['POST'])
    def import_family(self, request):
        """Import a node family from JSON"""
        serializer = NodeFamilyImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        import_data = serializer.validated_data['file']

        with transaction.atomic():
            try:
                family = NodeFamily.objects.create(
                    name=import_data['name'],
                    description=import_data.get('description', ''),
                    created_by=request.user.username
                )

                for version_data in import_data.get('versions', []):
                    NodeVersion.objects.create(
                        family=family,
                        version=version_data['version'],
                        state=version_data.get('state', 'draft'),
                        changelog=version_data.get('changelog', ''),
                        created_by=request.user.username
                    )

                return Response(
                    NodeFamilySerializer(family, context={'request': request}).data,
                    status=status.HTTP_201_CREATED
                )
            except KeyError as e:
                logger.error(f"Missing field in import: {str(e)}")
                return Response(
                    {"error": f"Missing required field: {str(e)}"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                logger.error(f"Import failed: {str(e)}")
                return Response(
                    {"error": "Failed to import family"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

    def destroy(self, request, *args, **kwargs):
        """Delete a node family if no published versions exist"""
        family = self.get_object()
        if family.has_deployed_versions():
            return Response(
                {"error": "Cannot delete family with deployed versions"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        family.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['GET'], url_path='full_structure')
    def full_structure(self, request, id=None):
        """
        Return the node family with all versions, parameters, and subnodes/parameter values.
        """
        family = self.get_object()
        serializer = self.get_serializer(family)
        return Response(serializer.data)


class VersionViewSet(viewsets.GenericViewSet,
                   mixins.ListModelMixin,
                   mixins.RetrieveModelMixin,
                   mixins.DestroyModelMixin,
                   mixins.CreateModelMixin):
    """
    ViewSet for NodeVersion operations within a family
    """
    queryset = NodeVersion.objects.all()
    serializer_class = NodeVersionSerializer
    lookup_field = 'version'
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        family_id = self.kwargs.get('family_pk') or self.kwargs.get('family_id')
        if not family_id:
            raise Exception("family_pk or family_id is required in the URL for this viewset.")
        return NodeVersion.objects.filter(
            family_id=family_id
        ).select_related('family').prefetch_related(
            Prefetch(
                'child_links',
                queryset=NodeVersionLink.objects.select_related('child_version', 'child_version__family').prefetch_related(
                    Prefetch(
                        'child_version__parameters',
                        queryset=NodeParameter.objects.select_related('parameter'),
                        to_attr='prefetched_parameters'
                    )
                ),
                to_attr='prefetched_child_links'
            ),
            Prefetch(
                'parameters',
                queryset=NodeParameter.objects.select_related('parameter'),
                to_attr='prefetched_parameters'
            )
        ).order_by('-version')

    def get_serializer_class(self):
        if self.action == 'create':
            return NodeVersionCreateSerializer
        return super().get_serializer_class()
    
    def create(self, request, family_id=None):
        """Create a new version in the family"""
        family = get_object_or_404(NodeFamily, id=family_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            # Require at least one active (published) version to create a new version
            active_version = family.versions.filter(state='published').order_by('-version').first()
            if not active_version:
                return Response(
                    {"error": "Cannot create a new version when there is no active (published) version for this family."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            max_version = family.versions.aggregate(Max('version'))['version__max'] or 0
            new_version = NodeVersion.objects.create(
                family=family,
                version=max_version + 1,
                state='draft',
                changelog=serializer.validated_data.get('changelog', ''),
                created_by=request.user.username
            )

            # Choose source: explicit source_version if provided, else use the active published version
            source_version_num = serializer.validated_data.get('source_version') or active_version.version
            if source_version_num is not None:
                source = get_object_or_404(
                    NodeVersion,
                    family=family,
                    version=source_version_num
                )
                # Copy script from source
                new_version.script = source.script
                new_version.save()
                # Copy parameters in bulk from the chosen source
                NodeParameter.objects.bulk_create([
                    NodeParameter(
                        node_version=new_version,
                        parameter=np.parameter,
                        value=np.value
                    )
                    for np in source.parameters.select_related('parameter').all()
                ])
                # Copy subnode links in bulk from the chosen source
                NodeVersionLink.objects.bulk_create([
                    NodeVersionLink(
                        parent_version=new_version,
                        child_version=link.child_version,
                        order=link.order
                    )
                    for link in source.child_links.all()
                ])

            return Response(
                NodeVersionSerializer(new_version, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )

    @action(detail=True, methods=['POST'])
    def link_subversion(self, request, family_id=None, version=None):
        """Link a subnode version to this version"""
        version_obj = self.get_object()
        subversion_id = request.data.get('subversion_id')
        order = request.data.get('order', 0)
        
        if not subversion_id:
            return Response(
                {"error": "subversion_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            subversion = NodeVersion.objects.get(id=subversion_id)
            if subversion.family == version_obj.family:
                raise ValidationError("Cannot link versions from the same family")
        except NodeVersion.DoesNotExist:
            return Response(
                {"error": "Subversion not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        if not version_obj.family.child_nodes.filter(id=subversion.family.id).exists():
            return Response(
                {"error": "Can only link versions from configured subnodes"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            NodeVersionLink.objects.create(
                parent_version=version_obj,
                child_version=subversion,
                order=order
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['POST'])
    def publish(self, request, family_id=None, version=None):
        """Publish this version and archive others"""
        version_obj = self.get_object()
        
        if version_obj.state == 'published':
            return Response(
                {"status": "Version is already published"},
                status=status.HTTP_200_OK
            )
        
        if not version_obj.script:
            return Response(
                {"error": "Cannot publish version without a script"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            # Archive other published versions
            NodeVersion.objects.filter(
                family_id=family_id,
                state='published'
            ).update(state='archived')
            
            version_obj.state = 'published'
            version_obj.save()
            
            version_obj.family.is_deployed = True
            version_obj.family.save()
            
            return Response(
                {"status": f"Version {version_obj.version} published successfully"},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['POST'])
    def rollback(self, request, family_id=None, version=None):
        """Rollback to a previous version"""
        target_version = get_object_or_404(
            NodeVersion.objects.filter(state__in=['published', 'archived']),
            family_id=family_id,
            version=version
        )

        with transaction.atomic():
            NodeVersion.objects.filter(
                family_id=family_id,
                state='published'
            ).update(state='archived')

            target_version.state = 'published'
            target_version.changelog = (
                f"Reactivated via rollback from v{self.get_active_version(family_id)}"
            )
            target_version.save()

            target_version.family.is_deployed = True
            target_version.family.save()

        return Response(
            NodeVersionSerializer(target_version, context={'request': request}).data,
            status=status.HTTP_200_OK
        )

    def destroy(self, request, *args, **kwargs):
        """Delete a version if it's not published and not the only version"""
        version_obj = self.get_object()
        
        if version_obj.state == 'published':
            return Response(
                {"error": "Cannot delete published versions"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
        if version_obj.family.versions.count() == 1:
            return Response(
                {"error": "Cannot delete the only version in family"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
        version_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class ExecutionViewSet(viewsets.GenericViewSet):
    """
    ViewSet for NodeExecution operations
    """
    queryset = NodeExecution.objects.all()
    serializer_class = NodeExecutionSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return super().get_queryset().select_related(
            'version__family'
        ).order_by('-started_at')

    @action(detail=True, methods=['POST'])
    def execute(self, request, family_id=None, version=None):
        """Execute a published version"""
        version_obj = get_object_or_404(
            NodeVersion, 
            family_id=family_id, 
            version=version
        )

        if version_obj.state != 'published':
            return Response(
                {"error": "Only published versions can be executed"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        execution = NodeExecution.objects.create(
            version=version_obj,
            status='queued',
            triggered_by=request.user.username,
            log=f"Execution queued by {request.user.username}"
        )

        return Response(
            NodeExecutionSerializer(execution, context={'request': request}).data,
            status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=['POST'], url_path='stop')
    def stop_execution(self, request, family_id=None, execution_id=None):
        """Stop a running execution"""
        execution = get_object_or_404(
            NodeExecution, 
            version__family_id=family_id, 
            id=execution_id
        )

        if execution.status != 'running':
            return Response(
                {"error": "Only running executions can be stopped"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        execution.status = 'stopped'
        execution.completed_at = timezone.now()
        execution.log += "\nManually stopped by user"
        execution.save()

        return Response(
            {"status": "Execution stopped"}, 
            status=status.HTTP_200_OK
        )
class VersionContentViewSet(viewsets.GenericViewSet):
    """
    ViewSet for version content management (scripts, parameters)
    """
    queryset = NodeVersion.objects.all()
    lookup_field = 'version'
    parser_classes = [MultiPartParser, JSONParser]
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return super().get_queryset().filter(
            family_id=self.kwargs['family_id']
        ).select_related('family')

    @action(detail=True, methods=['PATCH'], parser_classes=[MultiPartParser])
    def script(self, request, family_id=None, version=None):
        """Update the version's script file"""
        node_version = self.get_object()
        
        if node_version.state == 'published':
            return Response(
                {"error": "Cannot modify published versions"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ScriptUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        node_version.script = serializer.validated_data['script']
        node_version.save()
        
        return Response(
            {
                "status": "Script updated successfully",
                "script_url": request.build_absolute_uri(node_version.script.url)
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['POST'], url_path='add_parameter')
    def add_parameter(self, request, family_id=None, version=None):
        """Add parameters to this version using parameter IDs and create default ParameterValue for each subnode."""
        node_version = self.get_object()

        if node_version.state == 'published':
            return Response(
                {"error": "Cannot modify published versions"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate input format
        if not isinstance(request.data, dict) or 'parameter_ids' not in request.data:
            return Response(
                {"error": "Request must contain 'parameter_ids' array"},
                status=status.HTTP_400_BAD_REQUEST
            )

        param_ids = request.data.get('parameter_ids', [])
        if not isinstance(param_ids, list):
            return Response(
                {"error": "'parameter_ids' must be an array"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not param_ids:
            return Response(
                {"error": "'parameter_ids' cannot be empty"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                # Get valid parameters
                parameters = Parameter.objects.filter(
                    id__in=param_ids,
                    is_active=True
                ).only('id', 'key', 'default_value')

                # Check for missing parameters
                found_ids = {str(p.id) for p in parameters}
                missing_params = set(param_ids) - found_ids
                if missing_params:
                    return Response(
                        {"error": f"Parameters not found: {missing_params}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Check for existing parameters (as UUIDs)
                existing_params = set(
                    node_version.parameters.values_list('parameter_id', flat=True)
                )

                # Only create NodeParameter for parameters not already present
                new_params = [
                    NodeParameter(
                        node_version=node_version,
                        parameter=param,
                        value=param.default_value if hasattr(param, 'default_value') else None
                    )
                    for param in parameters
                    if param.id not in existing_params
                ]

                # Bulk create NodeParameter
                if new_params:
                    NodeParameter.objects.bulk_create(new_params)
                    added_count = len(new_params)
                else:
                    added_count = 0

                # Automatically create ParameterValue for each subnode for each new parameter
                from flow_builder_app.parameter.models import ParameterValue
                from flow_builder_app.subnode.models import SubNode

                subnodes = SubNode.objects.filter(node_family=node_version.family)
                paramvalue_objs = []
                for param in parameters:
                    if str(param.id) not in existing_params:
                        for subnode in subnodes:
                            # Only create if not already exists
                            if not ParameterValue.objects.filter(parameter=param, subnode=subnode).exists():
                                paramvalue_objs.append(ParameterValue(
                                    parameter=param,
                                    subnode=subnode,
                                    value=param.default_value
                                ))
                if paramvalue_objs:
                    ParameterValue.objects.bulk_create(paramvalue_objs)

                # Automatically create SubNodeParameterValue for each subnode for each new parameter
                from flow_builder_app.subnode.models import SubNode, SubNodeParameterValue

                subnodes = SubNode.objects.filter(node_family=node_version.family)
                sn_paramvalue_objs = []
                for param in parameters:
                    if str(param.id) not in existing_params:
                        for subnode in subnodes:
                            # Only create if not already exists
                            if not SubNodeParameterValue.objects.filter(parameter=param, subnode=subnode).exists():
                                sn_paramvalue_objs.append(SubNodeParameterValue(
                                    parameter=param,
                                    subnode=subnode,
                                    value=param.default_value
                                ))
                if sn_paramvalue_objs:
                    SubNodeParameterValue.objects.bulk_create(sn_paramvalue_objs)

                return Response(
                    {
                        "status": f"Successfully added {added_count} parameters",
                        "added_parameters": [
                            {
                                "id": str(param.parameter.id),
                                "key": param.parameter.key,
                                "value": param.value
                            }
                            for param in new_params
                        ],
                        "version": NodeVersionSerializer(
                            node_version,
                            context={'request': request}
                        ).data
                    },
                    status=status.HTTP_200_OK
                )

        except Exception as e:
            logger.error(f"Error adding parameters: {str(e)}", exc_info=True)
            return Response(
                {"error": "Internal server error while adding parameters"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )  
   
    @action(detail=True, methods=['POST'], url_path='remove_parameter')
    def remove_parameter(self, request, family_id=None, version=None):
        """Remove parameters from this version"""
        node_version = self.get_object()

        if node_version.state == 'published':
            return Response(
                {"error": "Cannot modify published versions"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        param_ids = request.data.get("parameter_ids", [])
        if not param_ids:
            return Response(
                {"error": "parameter_ids list is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            # Validate parameters exist before deletion
            existing_params = node_version.parameters.filter(
                parameter_id__in=param_ids
            ).values_list('parameter_id', flat=True)
            
            missing_params = set(param_ids) - set(existing_params)
            if missing_params:
                return Response(
                    {"error": f"Parameters not found: {missing_params}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            deleted_count, _ = node_version.parameters.filter(
                parameter_id__in=param_ids
            ).delete()

            return Response(
                {
                    "status": f"Removed {deleted_count} parameters",
                    "version": NodeVersionSerializer(
                        node_version, 
                        context={'request': request}
                    ).data
                },
                status=status.HTTP_200_OK
            )
    
    @action(detail=True, methods=['GET'])
    def subnodes(self, request, family_id=None, version=None):
        """Get all subnodes with their parameters for this version"""
        node_version = self.get_object()
        
        # Get all linked subnode versions
        subnode_links = node_version.child_links.select_related(
            'child_version',
            'child_version__family'
        ).prefetch_related(
            Prefetch(
                'child_version__nodeparameter_set',
                queryset=NodeParameter.objects.select_related('parameter'),
                to_attr='parameters'
            )
        ).all()

        response_data = []
        for link in subnode_links:
            subnode_version = link.child_version
            response_data.append({
                "link_id": str(link.id),
                "order": link.order,
                "version_info": {
                    "id": str(subnode_version.id),
                    "version": subnode_version.version,
                    "state": subnode_version.state,
                    "family": {
                        "id": str(subnode_version.family.id),
                        "name": subnode_version.family.name
                    },
                    "parameters": [
                        {
                            "parameter_id": str(np.parameter.id),
                            "key": np.parameter.key,
                            "value": np.value,
                            # Only include type if it exists in Parameter model
                            "type": np.parameter.type if hasattr(np.parameter, 'type') else None
                        }
                        for np in getattr(subnode_version, 'parameters', [])
                    ]
                }
            })

        return Response(response_data)
    
    def get_script(self, request, family_id=None, version=None):
        """
        GET /api/node-families/<family_id>/versions/<version>/script/
        """
        node_version = get_object_or_404(NodeVersion, family_id=family_id, version=version)
        
        if not node_version.script:
            return Response(
                {"error": "No script available for this version"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        return Response(
            {"script_url": request.build_absolute_uri(node_version.script.url)},
            status=status.HTTP_200_OK
        )
class DeploymentViewSet(viewsets.GenericViewSet):
    """
    ViewSet for handling version deployment operations
    Includes endpoints for deploying and undeploying versions
    """
    queryset = NodeVersion.objects.all()
    serializer_class = NodeVersionSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = 'version'

    def get_queryset(self):
        return super().get_queryset().filter(
            family_id=self.kwargs['family_id']
        ).select_related('family')

    @action(detail=True, methods=['post'])
    def deploy(self, request, family_id=None, version=None):
        """
        Deploy a specific version (set it to published state)
        This will automatically archive other published versions in the same family
        """
        version_obj = self.get_object()
        
        # Check if already deployed
        if version_obj.state == 'published':
            return Response(
                {"status": "Version is already deployed"},
                status=status.HTTP_200_OK
            )

        # Validate the version can be deployed
        if not version_obj.script:
            return Response(
                {"error": "Cannot deploy version without a script"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                # Archive other published versions in this family
                NodeVersion.objects.filter(
                    family_id=family_id,
                    state='published'
                ).exclude(pk=version_obj.pk).update(state='archived')
                
                # Publish this version
                version_obj.state = 'published'
                version_obj.save()
                
                # Update family deployment status
                version_obj.family.is_deployed = True
                version_obj.family.save()

                logger.info(
                    f"Version {version_obj.version} of family {version_obj.family.name} "
                    f"deployed by user {request.user.username}"
                )
                
                return Response(
                    {
                        "status": f"Version {version_obj.version} deployed successfully",
                        "version": NodeVersionSerializer(version_obj, context={'request': request}).data
                    },
                    status=status.HTTP_200_OK
                )
                
        except Exception as e:
            logger.error(f"Deployment failed: {str(e)}")
            return Response(
                {"error": "Failed to deploy version"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def undeploy(self, request, family_id=None, version=None):
        """
        Undeploy a version (set it back to draft state)
        This will mark the family as not deployed if no other published versions exist
        """
        version_obj = self.get_object()
        
        # Check if already undeployed
        if version_obj.state != 'published':
            return Response(
                {"status": "Version is already undeployed"},
                status=status.HTTP_200_OK
            )

        try:
            with transaction.atomic():
                # Set version back to draft
                version_obj.state = 'draft'
                version_obj.save()
                
                # Check if any published versions remain
                has_published = NodeVersion.objects.filter(
                    family_id=family_id,
                    state='published'
                ).exists()
                
                # Update family deployment status
                version_obj.family.is_deployed = has_published
                version_obj.family.save()

                logger.info(
                    f"Version {version_obj.version} of family {version_obj.family.name} "
                    f"undeployed by user {request.user.username}"
                )
                
                return Response(
                    {
                        "status": f"Version {version_obj.version} undeployed successfully",
                        "family_status": "deployed" if has_published else "not deployed"
                    },
                    status=status.HTTP_200_OK
                )
                
        except Exception as e:
            logger.error(f"Undeployment failed: {str(e)}")
            return Response(
                {"error": "Failed to undeploy version"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def status(self, request, family_id=None):
        """
        Get deployment status for a family
        """
        family = get_object_or_404(NodeFamily, id=family_id)
        published_versions = NodeVersion.objects.filter(
            family_id=family_id,
            state='published'
        ).order_by('-version')
        
        data = {
            "family_id": str(family.id),
            "family_name": family.name,
            "is_deployed": family.is_deployed,
            "published_versions": NodeVersionSerializer(
                published_versions,
                many=True,
                context={'request': request}
            ).data if published_versions.exists() else []
        }
        
        return Response(data, status=status.HTTP_200_OK)