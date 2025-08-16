import logging
import uuid
import json
from collections import defaultdict
from django.db.models import Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from flow_builder_app.subnode.models import SubNodeParameterValue
from .models import SubNode
from flow_builder_app.parameter.models import ParameterValue,Parameter
from flow_builder_app.node.models import NodeFamily
from .serializers import SubNodeSerializer, ParameterValueUpdateSerializer, SubNodeCreateSerializer

logger = logging.getLogger(__name__)


class SubNodeViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    queryset = SubNode.objects.all()
    serializer_class = SubNodeSerializer
    lookup_field = 'id'

    def get_serializer_class(self):
        if self.action == 'create':
            return SubNodeCreateSerializer
        return SubNodeSerializer

    # ---------- Helper Methods ----------
    def _get_original(self, subnode):
        return subnode.original or subnode

    def _max_version(self, original_ref):
        return (
            SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id))
            .aggregate(Max('version'))['version__max'] or 1
        )

    def _copy_parameters(self, source_subnode, target_subnode):
        for pv in source_subnode.parameter_values.all():
            ParameterValue.objects.create(
                parameter=pv.parameter,
                subnode=target_subnode,
                value=pv.value
            )

    def perform_create(self, serializer):
        family_id = self.kwargs.get('family_id') or self.request.data.get('node_family')
        node_family = get_object_or_404(NodeFamily, id=family_id)
        serializer.save(node_family=node_family)

    # ---------- CRUD Methods ----------
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data.pop('version', None)
        if 'node_family' not in data:
            return Response({"error": "node_family field is required"}, status=400)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        try:
            self.perform_create(serializer)
            new_subnode = serializer.instance

            # Get parameters through NodeVersion instead of NodeFamily
            latest_version = new_subnode.node_family.versions.order_by('-version').first()
            if latest_version:
                node_parameters = Parameter.objects.filter(
                    node_parameters__node_version=latest_version
                ).distinct()
                
                for param in node_parameters:
                    ParameterValue.objects.get_or_create(
                        parameter=param,
                        subnode=new_subnode,
                        defaults={'value': param.default_value}
                    )
            else:
                logger.warning(f"No versions found for node family {new_subnode.node_family.id}")

        except Exception as e:
            logger.error(f"Error creating subnode: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        subnode = self.get_object()
        original_ref = self._get_original(subnode)
        
        # Get all versions of this SubNode
        versions_qs = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id)
        ).order_by('version')
        
        # Get active version if exists
        active_version_obj = versions_qs.filter(is_deployed=True).order_by('-version').first()
        active_version_num = active_version_obj.version if active_version_obj else None
        original_version_num = versions_qs.first().version if versions_qs.exists() else None

        # Prefetch all parameters for the node family to optimize queries
        node_family_parameters = Parameter.objects.filter(
            node_parameters__node_version__family=subnode.node_family
        ).distinct().prefetch_related('node_parameters')

        versions_list = []
        for version in versions_qs:
            # Get explicitly set parameter values for this version
            param_values_qs = ParameterValue.objects.filter(subnode=version).select_related('parameter')
            explicit_params = {pv.parameter.key: pv.value for pv in param_values_qs}
            
            # Build complete parameter list including inherited defaults
            all_parameters = []
            for param in node_family_parameters:
                # Check if this parameter exists in any version of the node family
                param_data = {
                    "parameter_key": param.key,
                    "value": explicit_params.get(param.key, param.default_value),
                    "default_value": param.default_value,
                    "datatype": param.datatype,
                    "source": "subnode" if param.key in explicit_params else "default"
                }
                all_parameters.append(param_data)

            versions_list.append({
                "id": str(version.id),
                "version": version.version,
                "is_deployed": version.is_deployed,
                "is_editable": version.is_editable,
                "updated_at": version.updated_at.isoformat() if version.updated_at else None,
                "updated_by": getattr(version, 'updated_by', '') or "",
                "version_comment": version.version_comment or "",
                "parameter_values": all_parameters  # Now includes all parameters
            })

        response_data = {
            "id": str(original_ref.id),
            "name": original_ref.name,
            "description": original_ref.description or "",
            "node_family": {
                "id": str(original_ref.node_family.id),
                "name": original_ref.node_family.name
            } if original_ref.node_family else None,
            "active_version": active_version_num,
            "original_version": original_version_num,
            "created_at": original_ref.created_at.isoformat() if original_ref.created_at else None,
            "created_by": getattr(original_ref, 'created_by', '') or "",
            "versions": versions_list,
        }
        return Response(response_data)

    def update(self, request, *args, **kwargs):
        subnode = self.get_object()
        if subnode.is_deployed:
            return Response({"error": "Editing deployed subnode not allowed. Create a new version instead."},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(subnode, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        from_node_id = request.data.get('from_node')
        selected_subnode = request.data.get('selected_subnode')

        if from_node_id:
            try:
                from_flownode = FlowNode.objects.get(id=from_node_id)
                instance.from_node = from_flownode

                if not Edge.objects.filter(flow=instance.flow,
                                           from_node=from_flownode.node,
                                           to_node=instance.node).exists():
                    Edge.objects.create(flow=instance.flow,
                                        from_node=from_flownode.node,
                                        to_node=instance.node)
            except FlowNode.DoesNotExist:
                return Response({'error': 'Source FlowNode not found.'}, status=status.HTTP_400_BAD_REQUEST)

        if selected_subnode:
            if not SubNode.objects.filter(id=selected_subnode, node=instance.node).exists():
                return Response({'error': 'Selected subnode must belong to the node.'},
                                status=status.HTTP_400_BAD_REQUEST)
            instance.selected_subnode_id = selected_subnode

        instance.save()
        return Response(self.get_serializer(instance).data)

    # ---------- Version Methods ----------
    @action(detail=True, methods=['post'], url_path='activate_version/(?P<version_number>[^/.]+)')
    def activate_version(self, request, version_number=None, **kwargs):
        subnode = self.get_object()
        version_number = int(version_number)
        original_ref = self._get_original(subnode)

        SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id)).update(is_deployed=False)
        updated = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id),
                                         version=version_number).update(is_deployed=True)
        version_to_activate = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id),
                                                     version=version_number).first()
        if not updated or not version_to_activate:
            return Response({"error": "Version not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": str(version_to_activate.id),
            "name": version_to_activate.name,
            "is_deployed": version_to_activate.is_deployed,
            "version": version_to_activate.version,
            "message": f"Subnode '{version_to_activate.name}' version {version_to_activate.version} activated successfully."
        })

    @action(detail=True, methods=['post'], url_path='undeploy_version/(?P<version_number>[^/.]+)')
    def undeploy_version(self, request, version_number=None, **kwargs):
        subnode = self.get_object()
        version_number = int(version_number)
        original_ref = self._get_original(subnode)

        version_obj = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id),
                                             version=version_number).first()
        if not version_obj:
            return Response({"error": "Version not found"}, status=status.HTTP_404_NOT_FOUND)
        if not version_obj.is_deployed:
            return Response({"error": "Version is already undeployed."}, status=status.HTTP_400_BAD_REQUEST)

        version_obj.is_deployed = False
        version_obj.save()
        return Response({"message": f"Subnode '{version_obj.name}' version {version_obj.version} undeployed successfully."})

    @action(detail=True, methods=['delete'], url_path='delete_version/(?P<version_number>[^/.]+)')
    def delete_version(self, request, version_number=None, **kwargs):
        subnode = self.get_object()
        version_number = int(version_number)
        original_ref = self._get_original(subnode)

        version_obj = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id),
                                             version=version_number).first()
        if not version_obj:
            return Response({"error": "Version not found"}, status=status.HTTP_404_NOT_FOUND)
        if version_obj.is_deployed:
            return Response({"error": "Cannot delete deployed version."}, status=status.HTTP_400_BAD_REQUEST)

        version_obj.delete()
        return Response({"message": f"Subnode '{version_obj.name}' version {version_obj.version} deleted successfully."})

    @action(detail=True, methods=['post'], url_path='create_editable_version')
    def create_editable_version(self, request, id=None):
        subnode = self.get_object()
        if not subnode.is_deployed:
            return Response({"error": "Version is already editable."}, status=status.HTTP_400_BAD_REQUEST)

        version_comment = request.data.get('version_comment')
        if not version_comment:
            return Response({"error": "version_comment is required"}, status=status.HTTP_400_BAD_REQUEST)

        original_ref = self._get_original(subnode)
        new_version_number = self._max_version(original_ref) + 1

        new_subnode = SubNode.objects.create(
            original=original_ref,
            node_family=subnode.node_family,
            name=subnode.name,
            description=subnode.description,
            version_comment=version_comment,
            version=new_version_number,
            is_deployed=False,
        )

        self._copy_parameters(subnode, new_subnode)

        serializer = self.get_serializer(new_subnode)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ---------- List Method ----------
    def list(self, request, *args, **kwargs):
        all_subnodes = SubNode.objects.all().select_related('node_family')
        grouped = defaultdict(list)
        originals = {}

        for sn in all_subnodes:
            orig_id = sn.original.id if sn.original else sn.id
            originals[orig_id] = sn.original or sn
            grouped[orig_id].append(sn)

        response = []
        for orig_id, versions in grouped.items():
            original = originals[orig_id]
            versions_sorted = sorted(versions, key=lambda v: v.version)

            active_version_obj = max(
                (v for v in versions_sorted if v.is_deployed),
                key=lambda v: v.version,
                default=None
            )
            active_version_num = active_version_obj.version if active_version_obj else None
            original_version_num = versions_sorted[0].version if versions_sorted else None

            versions_list = []
            for v in versions_sorted:
                param_values_qs = SubNodeParameterValue.objects.filter(subnode=v)
                param_values = [dict(
                    id=str(pv.id),
                    parameter_key=pv.parameter.key,
                    value=pv.value
                ) for pv in param_values_qs]

                versions_list.append({
                    "id": str(v.id),
                    "version": v.version,
                    "is_deployed": v.is_deployed,
                    "is_editable": v.is_editable,
                    "updated_at": v.updated_at.isoformat() if v.updated_at else None,
                    "updated_by": getattr(v, 'updated_by', '') or "",
                    "version_comment": v.version_comment or "",
                    "parameter_values": param_values,
                })

            response.append({
                "id": str(original.id),
                "name": original.name,
                "description": original.description or "",
                "node_family": str(original.node_family.id) if original.node_family else None,
                "active_version": active_version_num,
                "original_version": original_version_num,
                "created_at": original.created_at.isoformat() if original.created_at else None,
                "created_by": getattr(original, 'created_by', '') or "",
                "versions": versions_list,
            })

        return Response(response)

    @action(detail=True, methods=['patch'], url_path='update_parameter_values')
    def update_parameter_values(self, request, *args, **kwargs):
        subnode = self.get_object()

        if not subnode.is_editable:
            return Response(
                {"error": "This subnode version is deployed and cannot be edited."},
                status=status.HTTP_400_BAD_REQUEST
            )

        parameter_values = request.data.get('parameter_values', [])
        updated = []
        errors = []

        for pv in parameter_values:
            pv_id = pv.get('id')
            value = pv.get('value')

            if not pv_id:
                errors.append({"error": "ParameterValue id is required"})
                continue

            try:
                param_val = subnode.parameter_values.get(id=pv_id)
                param_val.value = value
                param_val.save()
                updated.append({
                    "id": str(param_val.id),
                    "parameter_key": param_val.parameter.key,
                    "value": param_val.value
                })
            except ParameterValue.DoesNotExist:
                errors.append({"error": f"ParameterValue {pv_id} not found for this subnode"})

        return Response({"updated": updated, "errors": errors})


    # @action(detail=True, methods=['patch'], url_path='update_parameter_values')
    # def update_parameter_values(self, request, *args, **kwargs):
    #     subnode = self.get_object()

    #     if not subnode.is_editable:
    #         return Response(
    #             {"error": "This subnode version is deployed and cannot be edited."},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     parameter_values = request.data.get('parameter_values', [])
    #     updated = []
    #     errors = []

    #     for pv in parameter_values:
    #         param_id = pv.get('parameter')  # Expecting parameter UUID from frontend
    #         value = pv.get('value')

    #         if not param_id:
    #             errors.append({"error": "Parameter ID is required"})
    #             continue

    #         try:
    #             # Try to get the ParameterValue for this subnode + parameter
    #             param_val, created = ParameterValue.objects.get_or_create(
    #                 subnode=subnode,
    #                 parameter_id=param_id,
    #                 defaults={'value': value}
    #             )

    #             if not created:
    #                 param_val.value = value
    #                 param_val.save()

    #             updated.append({
    #                 "id": str(param_val.id),
    #                 "parameter": str(param_val.parameter.id),
    #                 "parameter_key": param_val.parameter.key,
    #                 "value": param_val.value,
    #                 "created": created
    #             })

    #         except Parameter.DoesNotExist:
    #             errors.append({"error": f"Parameter {param_id} does not exist"})

    #     return Response({"updated": updated, "errors": errors})


    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, **kwargs):
        subnode = self.get_object()
        original_ref = self._get_original(subnode)

        export_version = (
            SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id), is_deployed=True)
            .order_by('-version').first()
            or SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id))
            .order_by('-version').first()
        )

        if not export_version:
            return Response({'error': 'No subnode version found.'}, status=status.HTTP_404_NOT_FOUND)

        data = self.get_serializer(export_version).data

        def convert_uuids(obj):
            if isinstance(obj, dict):
                return {k: convert_uuids(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert_uuids(v) for v in obj]
            if isinstance(obj, uuid.UUID):
                return str(obj)
            return obj

        data = convert_uuids(data)

        response = HttpResponse(json.dumps(data, indent=2), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="subnode_{export_version.id}_v{export_version.version}.json"'
        return response

    @action(detail=False, methods=['post'], url_path='import')
    def import_subnode(self, request):
        name = request.data.get('name')
        node_id = request.data.get('node_id')
        description = request.data.get('description', '')
        version_comment = request.data.get('version_comment', '')

        if not name or not node_id:
            return Response({"error": "name and node_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        node = get_object_or_404(NodeFamily, id=node_id)
        subnode = SubNode.objects.create(
            node=node,
            name=name,
            is_selected=False,
            is_deployed=False,
            version=1,
            original=None,
            description=description,
            version_comment=version_comment
        )
        return Response(self.get_serializer(subnode).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='delete_all_versions')
    def delete_all_versions(self, request, **kwargs):
        subnode = self.get_object()
        original_ref = self._get_original(subnode)

        if SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id), is_deployed=True).exists():
            return Response({"error": "Cannot delete all versions while one is deployed. Please undeploy first."},
                            status=status.HTTP_400_BAD_REQUEST)

        deleted_count, _ = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id)).delete()

        return Response({"message": f"All {deleted_count} versions deleted successfully for subnode '{original_ref.name}'."})

    @action(detail=True, methods=['get'], url_path='parameter_values_with_ids')
    def parameter_values_with_ids(self, request, id=None):
        subnode = self.get_object()
        pvs = subnode.parameter_values.select_related('parameter').all()
        data = [
            {
                "id": str(pv.id),
                "parameter_key": pv.parameter.key,
                "value": pv.value
            }
            for pv in pvs
        ]
        return Response(data)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone_subnode(self, request, *args, **kwargs):
        original_subnode = self.get_object()
        original_ref = original_subnode.original or original_subnode

        version_to_clone = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id),
            is_deployed=True
        ).order_by('-version').first()

        if not version_to_clone:
            version_to_clone = SubNode.objects.filter(
                Q(original=original_ref) | Q(id=original_ref.id)
            ).order_by('-version').first()

        if not version_to_clone:
            return Response({"error": "No version found to clone"}, status=status.HTTP_400_BAD_REQUEST)

        cloned_subnode = SubNode.objects.create(
            id=uuid.uuid4(),
            name=f"{version_to_clone.name}_copy",
            description=version_to_clone.description,
            node=version_to_clone.node,
            version=1,
            is_deployed=False,
            original=None,
            version_comment=f"Cloned from subnode {version_to_clone.id}"
        )

        for pv in version_to_clone.parameter_values.all():
            ParameterValue.objects.create(
                id=uuid.uuid4(),
                subnode=cloned_subnode,
                parameter=pv.parameter,
                value=pv.value
            )

        serializer = self.get_serializer(cloned_subnode)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch'], url_path='edit_with_parameters')
    def edit_with_parameters(self, request, pk=None):
        subnode = self.get_object()

        # Step 1: Update subnode fields
        subnode_serializer = SubNodeSerializer(subnode, data=request.data, partial=True)
        if subnode_serializer.is_valid():
            subnode_serializer.save()
        else:
            return Response(subnode_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Step 2: Update parameter values if provided
        parameter_values_data = request.data.get('parameter_values', [])
        for param_data in parameter_values_data:
            try:
                param_value = ParameterValue.objects.get(id=param_data['id'], subnode=subnode)
                param_serializer = ParameterValueUpdateSerializer(param_value, data={'value': param_data['value']}, partial=True)
                if param_serializer.is_valid():
                    param_serializer.save()
                else:
                    return Response(param_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            except ParameterValue.DoesNotExist:
                return Response({"error": f"ParameterValue {param_data['id']} not found for this subnode"},
                                status=status.HTTP_404_NOT_FOUND)

        return Response({
            "message": "Subnode and parameter values updated successfully",
            "subnode": subnode_serializer.data
        }, status=status.HTTP_200_OK)
