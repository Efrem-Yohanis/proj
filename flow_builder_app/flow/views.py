from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction

from .models import Flow, FlowNode, Edge, ExecutionLog, FlowNodeParameter

from .serializers import (
   EdgeNodeSerializer, EdgeSerializer, FlowNodeSerializer, FlowSerializer, FlowGraphSerializer,ExecutionLogSerializer
)

from flow_builder_app.node.models import NodeExecution
from flow_builder_app.node.serializers import NodeExecutionSerializer

class FlowViewSet(viewsets.ModelViewSet):
    queryset = Flow.objects.all().order_by('-created_at')
    serializer_class = FlowSerializer

    @action(detail=True, methods=['post'])
    def deploy(self, request, pk=None):
        flow = self.get_object()
        if flow.is_deployed:
            return Response({'detail': 'Flow already deployed'}, status=status.HTTP_400_BAD_REQUEST)
        flow.status = 'deployed'
        flow.save()
        return Response({'detail': f'Flow {flow.name} deployed'})

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        flow = self.get_object()
        if not flow.is_deployed:
            return Response({'detail': 'Flow must be deployed before running'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create FlowExecution instance
        exec_instance = FlowExecution.objects.create(
            flow=flow,
            status='running',
            triggered_by=request.user.username,
        )
        # Optionally, here trigger async task to run the flow
        return Response({'execution_id': str(exec_instance.id), 'status': exec_instance.status})


class FlowNodeViewSet(viewsets.ModelViewSet):
    queryset = FlowNode.objects.all()
    serializer_class = FlowNodeSerializer

    def create(self, request, *args, **kwargs):
        with transaction.atomic():
            # Auto-assign order if not provided
            flow_id = request.data.get('flow_id')
            if 'order' not in request.data and flow_id:
                last_order = FlowNode.objects.filter(flow_id=flow_id).aggregate(Max('order'))['order__max'] or 0
                request.data['order'] = last_order + 1
            return super().create(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def set_subnode(self, request, pk=None):
        node = self.get_object()
        subnode_id = request.data.get('subnode_id')
        from flow_builder_app.subnode.models import SubNode

        try:
            subnode = SubNode.objects.get(pk=subnode_id)
        except SubNode.DoesNotExist:
            return Response({'detail': 'Subnode not found'}, status=status.HTTP_404_NOT_FOUND)

        node.selected_subnode_id = subnode.id
        node.save(update_fields=['selected_subnode'])
        return Response({'detail': f'Subnode {subnode_id} selected for node {node.id}'})


class FlowEdgeViewSet(viewsets.ModelViewSet):
    queryset = Edge.objects.all()
    serializer_class = EdgeSerializer


class FlowExecutionViewSet(viewsets.ModelViewSet):
    queryset = ExecutionLog.objects.all()
    serializer_class = ExecutionLogSerializer


class NodeExecutionLogViewSet(viewsets.ModelViewSet):
    queryset = NodeExecution.objects.all()
    serializer_class = NodeExecutionSerializer
