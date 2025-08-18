from rest_framework import serializers
from flow_builder_app.subnode.models import SubNode
from flow_builder_app.node.models import NodeFamily
from flow_builder_app.parameter.models import Parameter, ParameterValue
from .models import Flow, FlowNode, ExecutionLog, Edge


#EdgeNodeSerializer, EdgeSerializer, FlowNodeSerializer, FlowSerializer, FlowGraphSerializer,ExecutionLogSerializer

class EdgeNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Edge
        fields = ['id', 'from_node', 'to_node', 'condition']
        read_only_fields = ['id']


class EdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Edge
        fields = ['id', 'flow', 'from_node', 'to_node', 'condition', 'last_updated_by', 'last_updated_at']
        read_only_fields = ['id', 'last_updated_at']

    def validate(self, data):
        flow = data.get('flow')
        from_node = data.get('from_node')
        to_node = data.get('to_node')

        qs = Edge.objects.filter(flow=flow, from_node=from_node, to_node=to_node)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Edge with this flow, from_node, and to_node already exists.")
        return data


class FlowNodeSerializer(serializers.ModelSerializer):
    node_family = serializers.StringRelatedField(read_only=True)
    node_family_id = serializers.PrimaryKeyRelatedField(
        queryset=NodeFamily.objects.all(), source='node_family', write_only=True
    )
    flow_id = serializers.PrimaryKeyRelatedField(
        queryset=Flow.objects.all(), source='flow', write_only=True
    )
    from_node_id = serializers.PrimaryKeyRelatedField(
        queryset=FlowNode.objects.all(), source='from_node', allow_null=True, required=False
    )
    selected_subnode_id = serializers.PrimaryKeyRelatedField(
        queryset=SubNode.objects.all(), source='selected_subnode', allow_null=True, required=False
    )
    outgoing_edges = serializers.SerializerMethodField()
    incoming_edges = serializers.SerializerMethodField()

    class Meta:
        model = FlowNode
        fields = [
            'id', 'order', 'node_family', 'node_family_id', 'flow_id',
            'from_node_id', 'selected_subnode_id', 'outgoing_edges', 'incoming_edges'
        ]
        read_only_fields = ['id', 'outgoing_edges', 'incoming_edges', 'node_family']

    def validate_selected_subnode_id(self, value):
        node_family_id = self.initial_data.get('node_family_id') or (self.instance.node_family.id if self.instance else None)
        if value and node_family_id and str(value.node_family.id) != str(node_family_id):
            raise serializers.ValidationError("Selected subnode must belong to the same node family.")
        return value

    def get_outgoing_edges(self, obj):
        edges = Edge.objects.filter(from_node=obj)
        return EdgeNodeSerializer(edges, many=True).data

    def get_incoming_edges(self, obj):
        edges = Edge.objects.filter(to_node=obj)
        return EdgeNodeSerializer(edges, many=True).data


class FlowSerializer(serializers.ModelSerializer):
    flow_nodes = FlowNodeSerializer(many=True, read_only=True)
    is_running = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    last_updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Flow
        fields = '__all__'
        read_only_fields = [
            'id', 'version', 'is_running', 'created_at', 'updated_at', 'last_updated_at'
        ]


class FlowGraphSerializer(serializers.ModelSerializer):
    nodes = serializers.SerializerMethodField()
    edges = EdgeSerializer(many=True, read_only=True, source='edges')

    class Meta:
        model = Flow
        fields = ['id', 'name', 'version', 'nodes', 'edges']

    def get_nodes(self, flow):
        flow_nodes = flow.flow_nodes.select_related('node_family', 'selected_subnode').order_by('order')
        return [
            {
                "flow_node_id": str(fn.id),
                "id": str(fn.node_family.id),
                "name": fn.node_family.name,
                "order": fn.order,
                "selected_subnode_id": str(fn.selected_subnode.id) if fn.selected_subnode else None,
                "outgoing_edges": EdgeNodeSerializer(Edge.objects.filter(from_node=fn), many=True).data,
                "incoming_edges": EdgeNodeSerializer(Edge.objects.filter(to_node=fn), many=True).data,
            }
            for fn in flow_nodes
        ]


class ExecutionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExecutionLog
        fields = '__all__'
        read_only_fields = ['id', 'started_at', 'completed_at', 'last_updated_at']
