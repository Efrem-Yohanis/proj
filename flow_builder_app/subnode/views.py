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
from django.db import transaction

from flow_builder_app.subnode.models import SubNodeParameterValue
from .models import SubNode
from flow_builder_app.parameter.models import ParameterValue,Parameter
from flow_builder_app.node.models import NodeFamily, NodeVersion  # { added NodeVersion }
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
        # Save created_by and updated_by from request.user
        username = getattr(self.request.user, "username", "") or ""
        serializer.save(node_family=node_family, created_by=username, updated_by=username)

    # ---------- CRUD Methods ----------
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data.pop('version', None)
        if 'node_family' not in data:
            return Response({"error": "node_family field is required"}, status=400)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                self.perform_create(serializer)
                new_subnode = serializer.instance

                # Collect all unique parameter IDs used by any NodeVersion in this family
                from flow_builder_app.node.models import NodeParameter

                param_ids_qs = NodeParameter.objects.filter(
                    node_version__family=new_subnode.node_family
                ).values_list('parameter_id', flat=True).distinct()
                param_ids = list(param_ids_qs)

                if param_ids:
                    # Fetch Parameter objects for defaults
                    parameters = list(Parameter.objects.filter(id__in=param_ids).only('id', 'default_value'))

                    # Create missing ParameterValue objects
                    existing_pv_ids = set(
                        ParameterValue.objects.filter(subnode=new_subnode, parameter_id__in=param_ids)
                        .values_list('parameter_id', flat=True)
                    )
                    pv_objs = [
                        ParameterValue(
                            parameter=param,
                            subnode=new_subnode,
                            value=param.default_value
                        )
                        for param in parameters
                        if param.id not in existing_pv_ids
                    ]
                    if pv_objs:
                        ParameterValue.objects.bulk_create(pv_objs)

                    # Create missing SubNodeParameterValue objects
                    from flow_builder_app.subnode.models import SubNodeParameterValue
                    existing_snpv_ids = set(
                        SubNodeParameterValue.objects.filter(subnode=new_subnode, parameter_id__in=param_ids)
                        .values_list('parameter_id', flat=True)
                    )
                    snpv_objs = [
                        SubNodeParameterValue(
                            parameter=param,
                            subnode=new_subnode,
                            value=param.default_value
                        )
                        for param in parameters
                        if param.id not in existing_snpv_ids
                    ]
                    if snpv_objs:
                        SubNodeParameterValue.objects.bulk_create(snpv_objs)
                else:
                    logger.info(f"No parameters found for node family {new_subnode.node_family.id}")

        except Exception as e:
            logger.error(f"Error creating subnode: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        subnode = self.get_object()
        original_ref = self._get_original(subnode)

        # --- get subnode-version entries (these are the SubNode model versions) ---
        subnode_versions_qs = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id)
        ).order_by('version')

        # --- get all NodeVersions for the family (these are the node-version axes) ---
        from flow_builder_app.node.models import NodeVersion
        node_versions = NodeVersion.objects.filter(family=subnode.node_family).order_by('version')

        # active/original info derived from SubNode versions
        active_version_obj = subnode_versions_qs.filter(is_deployed=True).order_by('-version').first()
        active_version_num = active_version_obj.version if active_version_obj else None
        original_version_num = subnode_versions_qs.first().version if subnode_versions_qs.exists() else None

        # Helper to build the version object (subnode-level + nodeversion values)
        def build_version_data(sn_version):
            pv_qs = ParameterValue.objects.filter(subnode=sn_version).select_related('parameter')
            snpv_qs = SubNodeParameterValue.objects.filter(subnode=sn_version).select_related('parameter')

            # Build maps of subnode-level parameter key -> (value, id)
            subnode_override_map = {}
            subnode_override_id_map = {}
            for pv in pv_qs:
                subnode_override_map[pv.parameter.key] = pv.value
                subnode_override_id_map[pv.parameter.key] = str(pv.id)
            for spv in snpv_qs:
                # don't overwrite ParameterValue id/value if present
                subnode_override_map.setdefault(spv.parameter.key, spv.value)
                subnode_override_id_map.setdefault(spv.parameter.key, str(spv.id))

            nodeversion_values = []
            for nv in node_versions:
                node_params = nv.parameters.select_related('parameter').all()
                params_for_nv = []
                for np in node_params:
                    p = np.parameter
                    # prefer subnode override for this sn_version, then NodeParameter.value, then default
                    if p.key in subnode_override_map:
                        value = subnode_override_map[p.key]
                        params_for_nv.append({
                            "parameter_values_id": subnode_override_id_map.get(p.key),   # new field
                            "parameter_key": p.key,
                            "value": value,
                            "default_value": getattr(p, 'default_value', None),
                            "datatype": getattr(p, 'datatype', None),
                            "source": "version_override"
                        })
                    else:
                        params_for_nv.append({
                            "parameter_key": p.key,
                            "value": np.value if np.value is not None else getattr(p, 'default_value', None),
                            "default_value": getattr(p, 'default_value', None),
                            "datatype": getattr(p, 'datatype', None),
                            "source": "version"
                        })

                nodeversion_values.append({
                    "node_version": nv.version,
                    "parameter_values": params_for_nv
                })

            return {
                "id": str(sn_version.id),
                "version": sn_version.version,
                "is_deployed": sn_version.is_deployed,
                "is_editable": sn_version.is_editable,
                "updated_at": sn_version.updated_at.isoformat() if sn_version.updated_at else None,
                "updated_by": getattr(sn_version, 'updated_by', '') or "",
                "version_comment": sn_version.version_comment or "",
                "parameter_values_by_nodeversion": nodeversion_values
                # removed "parameter_values" array as requested
            }

        # Build published_version (deployed) or last_version (fallback)
        published_version_obj = None
        last_version_obj = None

        if active_version_obj:
            published_version_obj = build_version_data(active_version_obj)
        else:
            latest = subnode_versions_qs.order_by('-version').first()
            if latest:
                last_version_obj = build_version_data(latest)

        # Build full versions list (unchanged)
        versions_list = [build_version_data(sn_version) for sn_version in subnode_versions_qs]

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
            "published": True if active_version_num is not None else False,
            # include either published_version or last_version (not both)
            **({"published_version": published_version_obj} if published_version_obj is not None else {"last_version": last_version_obj}),
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
        serializer.save(updated_by=getattr(request.user, "username", "") or "")
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

        # set updated_by before saving
        instance.updated_by = getattr(request.user, "username", "") or ""
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
            created_by=getattr(request.user, "username", "") or "",
            updated_by=getattr(request.user, "username", "") or ""
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

        response_list = []
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

            response_list.append({
                "id": str(original.id),
                "name": original.name,
                "description": original.description or "",
                "node_family": str(original.node_family.id) if original.node_family else None,
                "node_family_name": original.node_family.name if original.node_family else None,
                "active_version": active_version_num,
                # NEW: indicates whether any version of this subnode is published/deployed
                "published": True if active_version_num is not None else False,
                "original_version": original_version_num,
                "created_at": original.created_at.isoformat() if original.created_at else None,
                "created_by": getattr(original, 'created_by', '') or "",
                "updated_at": original.updated_at.isoformat() if getattr(original, 'updated_at', None) else None,
                "updated_by": getattr(original, 'updated_by', '') or "",
                "versions": versions_list,
            })

        total_count = len(response_list)
        published_count = sum(1 for item in response_list if item.get('active_version') is not None)
        draft_count = total_count - published_count

        wrapped = {
            "total": total_count,
            "published": published_count,
            "draft": draft_count,
            "results": response_list
        }

        return Response(wrapped)

    @action(detail=True, methods=['patch'], url_path='update_parameter_values', url_name='update-parameter-values')
    def update_parameter_values(self, request, *args, **kwargs):
        subnode = self.get_object()
        param_updates = request.data.get('parameter_values', [])
        # optional: specific node version to sync (integer)
        target_version = request.data.get('version') or request.query_params.get('version')
        if target_version is not None:
            try:
                target_version = int(target_version)
            except (ValueError, TypeError):
                return Response({"error": "Invalid 'version' value"}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []

        for item in param_updates:
            param_id = item.get('id')
            new_value = item.get('value')

            if not param_id or new_value is None:
                errors.append({"id": param_id, "error": "Both 'id' and 'value' are required"})
                continue

            obj = (
                ParameterValue.objects.filter(id=param_id, subnode=subnode).first()
                or SubNodeParameterValue.objects.filter(id=param_id, subnode=subnode).first()
            )

            if obj:
                obj.value = new_value
                obj.save(update_fields=["value"])

                # Sync NodeParameter values for this parameter ONLY when a specific target_version is provided.
                try:
                    if target_version is not None:
                        from flow_builder_app.node.models import NodeParameter, NodeVersion
                        try:
                            target_nv = NodeVersion.objects.get(family=subnode.node_family, version=target_version)
                        except NodeVersion.DoesNotExist:
                            errors.append({"id": param_id, "error": f"Target version {target_version} not found"})
                            continue

                        np_qs = NodeParameter.objects.filter(node_version=target_nv, parameter=obj.parameter)
                        if np_qs.exists():
                            np_qs.update(value=new_value)
                        else:
                            NodeParameter.objects.create(
                                node_version=target_nv,
                                parameter=obj.parameter,
                                value=new_value
                            )
                    # else: do not modify NodeParameter rows when version not specified
                except Exception:
                    logger.exception("Failed to sync NodeParameter for parameter %s", getattr(obj.parameter, 'id', None))

                updated.append({"id": str(obj.id), "value": obj.value})
            else:
                errors.append({"id": param_id, "error": "Not found for this subnode"})

        return Response(
            {"updated": updated, "errors": errors},
            status=status.HTTP_200_OK if updated else status.HTTP_400_BAD_REQUEST
        )
    @action(detail=False, methods=['post'], url_path='import')
    def import_subnode(self, request):
        """
        Import subnode(s).
        - Simple mode (backwards compatible): provide name and node_id -> creates a single subnode v1.
        - File/JSON mode: upload a JSON file or send JSON body with structure:
          {
            "name": "...",
            "node_id": "<family id>",
            "versions": [
              {
                "version": 1,
                "parameter_values": [
                  { "parameter_id": "<uuid>" or "parameter_key":"key", "value": "..." },
                  ...
                ]
              },
              ...
            ]
          }
        Optional "versions" array in the request (body or query param) restricts which versions to import.
        """
        # Backwards-compatible simple create
        if 'file' not in request.FILES and not request.data.get('file') and not request.content_type == 'application/json':
            # existing simple import (name, node_id)
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
                version_comment=version_comment,
                created_by=getattr(request.user, "username", "") or "",
                updated_by=getattr(request.user, "username", "") or ""
            )
            return Response(self.get_serializer(subnode).data, status=status.HTTP_201_CREATED)

        # File/JSON import mode
        # Load JSON from uploaded file or request body
        try:
            if 'file' in request.FILES:
                f = request.FILES['file']
                data = json.load(f)
            else:
                # request.data may already be parsed JSON
                data = request.data if isinstance(request.data, dict) else json.loads(request.body.decode('utf-8') or '{}')
        except Exception as e:
            return Response({"error": f"Invalid JSON file/body: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Required top-level fields
        name = data.get('name') or data.get('id') or data.get('subnode_name')
        node_id = data.get('node_id') or data.get('node_family') or data.get('nodeFamily') or data.get('family')
        if not name or not node_id:
            return Response({"error": "Imported data must include 'name' and 'node_id' (node family id)"}, status=status.HTTP_400_BAD_REQUEST)

        # Optional filter of versions to import
        requested_versions = request.data.get('versions') or data.get('versions_filter') or request.query_params.get('versions')
        if isinstance(requested_versions, str):
            # comma-separated string -> list of ints
            try:
                requested_versions = [int(x) for x in requested_versions.split(',') if x.strip()]
            except Exception:
                requested_versions = None

        # Build versions payload
        versions_payload = data.get('versions') or []
        if not isinstance(versions_payload, list) or not versions_payload:
            return Response({"error": "Imported JSON must contain 'versions' array with version objects"}, status=status.HTTP_400_BAD_REQUEST)

        # If requested_versions provided, filter payload
        if requested_versions:
            versions_payload = [v for v in versions_payload if v.get('version') in requested_versions]
            if not versions_payload:
                return Response({"error": "No matching versions found in import file for requested versions"}, status=status.HTTP_400_BAD_REQUEST)

        warnings = []
        created_versions = []
        try:
            with transaction.atomic():
                node_family = get_object_or_404(NodeFamily, id=node_id)
                # Determine original id for grouping: create the original SubNode as the lowest version in payload
                versions_payload_sorted = sorted(versions_payload, key=lambda x: x.get('version') or 1)
                first_version = versions_payload_sorted[0]
                original_subnode = SubNode.objects.create(
                    id=first_version.get('id') or uuid.uuid4(),
                    node_family=node_family,
                    name=name,
                    description=data.get('description', ''),
                    version=first_version.get('version', 1),
                    is_deployed=first_version.get('is_deployed', False),
                    original=None,
                    version_comment=first_version.get('version_comment', '') or data.get('version_comment', ''),
                    created_by=getattr(request.user, "username", "") or "",
                    updated_by=getattr(request.user, "username", "") or ""
                )

                # Create each version: first one already created as original (if multiple versions and first_version != others)
                created_versions.append(original_subnode.version)
                # Create parameter values for first version
                pv_list = first_version.get('parameter_values', []) or []
                for pv in pv_list:
                    # find parameter by id or key
                    param = None
                    if pv.get('parameter_id'):
                        try:
                            from flow_builder_app.parameter.models import Parameter as ParamModel
                            param = ParamModel.objects.filter(id=pv.get('parameter_id')).first()
                        except Exception:
                            param = None
                    if not param and pv.get('parameter_key'):
                        from flow_builder_app.parameter.models import Parameter as ParamModel
                        param = ParamModel.objects.filter(key=pv.get('parameter_key')).first()
                    if not param:
                        warnings.append(f"Parameter not found for key/id: {pv.get('parameter_key') or pv.get('parameter_id')}, skipped")
                        continue
                    ParameterValue.objects.create(parameter=param, subnode=original_subnode, value=pv.get('value'))
                    # Also create SubNodeParameterValue if model exists
                    try:
                        SubNodeParameterValue.objects.create(parameter=param, subnode=original_subnode, value=pv.get('value'))
                    except Exception:
                        # model may not enforce; ignore
                        pass

                # Create remaining versions (if any)
                for ver in versions_payload_sorted[1:]:
                    vnum = ver.get('version') or (self._max_version(original_subnode) + 1)
                    new_subnode = SubNode.objects.create(
                        id=ver.get('id') or uuid.uuid4(),
                        node_family=node_family,
                        name=name,
                        description=ver.get('description', '') or data.get('description', ''),
                        version=vnum,
                        is_deployed=ver.get('is_deployed', False),
                        original=original_subnode,
                        version_comment=ver.get('version_comment', ''),
                        created_by=getattr(request.user, "username", "") or "",
                        updated_by=getattr(request.user, "username", "") or ""
                    )
                    created_versions.append(new_subnode.version)
                    for pv in ver.get('parameter_values', []) or []:
                        # resolve parameter
                        param = None
                        if pv.get('parameter_id'):
                            from flow_builder_app.parameter.models import Parameter as ParamModel
                            param = ParamModel.objects.filter(id=pv.get('parameter_id')).first()
                        if not param and pv.get('parameter_key'):
                            from flow_builder_app.parameter.models import Parameter as ParamModel
                            param = ParamModel.objects.filter(key=pv.get('parameter_key')).first()
                        if not param:
                            warnings.append(f"Parameter not found for key/id: {pv.get('parameter_key') or pv.get('parameter_id')} in version {vnum}, skipped")
                            continue
                        ParameterValue.objects.create(parameter=param, subnode=new_subnode, value=pv.get('value'))
                        try:
                            SubNodeParameterValue.objects.create(parameter=param, subnode=new_subnode, value=pv.get('value'))
                        except Exception:
                            pass

        except Exception as e:
            logger.exception("Import failed")
            return Response({"error": f"Import failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            "status": "imported",
            "original_id": str(original_subnode.id),
            "created_versions": created_versions,
            "warnings": warnings,
            "subnode": self.get_serializer(original_subnode).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, **kwargs):
        """
        Export subnode. Query params:
          versions=comma,separated,numbers   -> export those node-version numbers (node-version axis)
          delete=true                        -> after export, delete exported SubNode version records (skips deployed)
        If no versions param provided, behavior falls back to previous logic (exports deployed version or latest).
        """
        subnode = self.get_object()
        original_ref = self._get_original(subnode)

        # Determine requested versions (list of ints) or use default selection
        versions_param = request.query_params.get('versions')
        delete_flag = request.query_params.get('delete', 'false').lower() == 'true'

        # Build list of SubNode version instances to export
        if versions_param:
            try:
                req_versions = [int(x) for x in versions_param.split(',') if x.strip()]
            except Exception:
                return Response({"error": "Invalid 'versions' query param"}, status=status.HTTP_400_BAD_REQUEST)
            export_qs = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id), version__in=req_versions).order_by('version')
        else:
            # default: export deployed version else latest
            export_qs = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id), is_deployed=True).order_by('-version')
            if not export_qs.exists():
                export_qs = SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id)).order_by('-version')[:1]

        export_list = list(export_qs)

        if not export_list:
            return Response({'error': 'No subnode version found to export.'}, status=status.HTTP_404_NOT_FOUND)

        # Serialize all selected versions into a single structure
        data = self.get_serializer(export_list[0].original or export_list[0]).data
        # But replace 'versions' with only the requested export_list versions
        serialized_versions = [self.get_serializer(v).data for v in export_list]
        data['versions'] = serialized_versions

        # Optionally delete exported versions (skip deployed)
        deleted = []
        cannot_delete = []
        if delete_flag:
            try:
                with transaction.atomic():
                    for v in export_list:
                        if v.is_deployed:
                            cannot_delete.append(str(v.version))
                            continue
                        deleted.append(str(v.version))
                        v.delete()
            except Exception as e:
                logger.exception("Error deleting exported versions")
                return Response({"error": f"Exported but failed to delete versions: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = HttpResponse(json.dumps(data, indent=2), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="subnode_{original_ref.id}_export.json"'

        # attach metadata headers or include in body (we include in headers minimally)
        response['X-Exported-Versions'] = ','.join(str(v.version) for v in export_list)
        if deleted:
            response['X-Deleted-Versions'] = ','.join(deleted)
        if cannot_delete:
            response['X-Cannot-Delete'] = ','.join(cannot_delete)

        return response

    @action(detail=True, methods=['post'], url_path='clone_all')
    def clone_all_versions(self, request, *args, **kwargs):
        """
        Clone entire subnode (all versions). Optional request.data['new_name'] to override.
        Returns the new original subnode (with all versions copied, versions renumbered starting at 1).
        """
        orig = self.get_object()
        original_ref = self._get_original(orig)
        new_name = request.data.get('new_name') or f"{original_ref.name}_copy"
        created_by = getattr(request.user, "username", "") or ""

        # gather all versions sorted by version
        source_versions = list(SubNode.objects.filter(Q(original=original_ref) | Q(id=original_ref.id)).order_by('version'))
        if not source_versions:
            return Response({"error": "No versions found to clone"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # create new original as first version
                first_src = source_versions[0]
                new_original = SubNode.objects.create(
                    id=uuid.uuid4(),
                    node_family=first_src.node_family,
                    name=new_name,
                    description=first_src.description,
                    version=1,
                    is_deployed=False,
                    original=None,
                    version_comment=f"Cloned all versions from {original_ref.id}",
                    created_by=created_by,
                    updated_by=created_by
                )
                # copy parameter values for first version
                for pv in first_src.parameter_values.all():
                    ParameterValue.objects.create(
                        parameter=pv.parameter,
                        subnode=new_original,
                        value=pv.value
                    )
                    try:
                        SubNodeParameterValue.objects.create(
                            parameter=pv.parameter,
                            subnode=new_original,
                            value=pv.value
                        )
                    except Exception:
                        pass

                # create remaining versions (renumbered sequentially)
                for idx, src in enumerate(source_versions[1:], start=2):
                    new_v = SubNode.objects.create(
                        id=uuid.uuid4(),
                        node_family=src.node_family,
                        name=new_name,
                        description=src.description,
                        version=idx,
                        is_deployed=False,
                        original=new_original,
                        version_comment=src.version_comment or f"Cloned v{src.version} from {original_ref.id}",
                        created_by=created_by,
                        updated_by=created_by
                    )
                    for pv in src.parameter_values.all():
                        ParameterValue.objects.create(
                            parameter=pv.parameter,
                            subnode=new_v,
                            value=pv.value
                        )
                        try:
                            SubNodeParameterValue.objects.create(
                                parameter=pv.parameter,
                                subnode=new_v,
                                value=pv.value
                            )
                        except Exception:
                            pass

        except Exception as e:
            logger.exception("Failed to clone all versions")
            return Response({"error": f"Clone failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(self.get_serializer(new_original).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='clone_version/(?P<version_number>[^/.]+)')
    def clone_specific_version(self, request, version_number=None, *args, **kwargs):
        """
        Clone a specific subnode version. Optional request.data['new_name'] to override.
        Cloned subnode is created as a single-version original (version = 1).
        """
        orig = self.get_object()
        original_ref = self._get_original(orig)

        # Ensure version_number is valid integer
        try:
            version_number = int(version_number)
        except (ValueError, TypeError):
            return Response({"error": "Invalid version_number"}, status=status.HTTP_400_BAD_REQUEST)

        # Find source subnode of that version
        src = SubNode.objects.filter(
            Q(original=original_ref) | Q(id=original_ref.id),
            version=version_number
        ).first()

        if not src:
            return Response({"error": f"Version {version_number} not found"}, status=status.HTTP_404_NOT_FOUND)

        # New name (either user provided or auto-generated)
        new_name = request.data.get('new_name') or f"{src.name}_v{version_number}_copy"
        created_by = getattr(request.user, "username", "") or ""

        try:
            with transaction.atomic():
                new_subnode = SubNode.objects.create(
                    id=uuid.uuid4(),
                    node_family=src.node_family,
                    name=new_name,
                    description=src.description,
                    version=1,
                    is_deployed=False,
                    original=None,
                    version_comment=f"Cloned v{src.version} from {original_ref.id}",
                    created_by=created_by,
                    updated_by=created_by
                )

                # Copy parameter values
                for pv in src.parameter_values.all():
                    ParameterValue.objects.create(
                        parameter=pv.parameter,
                        subnode=new_subnode,
                        value=pv.value
                    )
                    try:
                        SubNodeParameterValue.objects.create(
                            parameter=pv.parameter,
                            subnode=new_subnode,
                            value=pv.value
                        )
                    except Exception:
                        # Safe ignore if SubNodeParameterValue not required
                        pass

        except Exception as e:
            logger.exception("Failed to clone specific version")
            return Response(
                {"error": f"Clone failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response(self.get_serializer(new_subnode).data, status=status.HTTP_201_CREATED)
