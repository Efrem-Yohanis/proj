from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid
import logging

from flow_builder_app.node.models import NodeFamily, NodeVersion
from flow_builder_app.subnode.models import SubNode, SubNodeParameterValue
from flow_builder_app.parameter.models import Parameter

logger = logging.getLogger(__name__)


class VersionedModel(models.Model):
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=255, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_at = models.DateTimeField(auto_now=True)
    version_comment = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ['-version']

    def save(self, *args, **kwargs):
        increment_version = kwargs.pop("increment_version", False)
        if self.pk and increment_version:
            self.version += 1
        if 'last_updated_by' in kwargs:
            self.last_updated_by = kwargs.pop('last_updated_by')
        if 'version_comment' in kwargs:
            self.version_comment = kwargs.pop('version_comment')
        super().save(*args, **kwargs)

    @classmethod
    def rollback(cls, pk, version):
        old = cls.objects.filter(pk=pk, version=version).first()
        if not old:
            raise ValidationError(f"Version {version} not found for {cls.__name__}")
        data = {
            f.name: getattr(old, f.name)
            for f in cls._meta.fields
            if f.name not in [
                'id', 'version', 'created_at', 'updated_at',
                'created_by', 'last_updated_by', 'last_updated_at'
            ]
        }
        obj = cls(**data)
        obj.version = old.version + 1
        return obj


class Flow(VersionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_deployed = models.BooleanField(default=False)
    is_running = models.BooleanField(default=False)

    class Meta:
        unique_together = ('name', 'version')

    def __str__(self):
        return f"{self.name} (v{self.version})"

    def can_run(self):
        return self.is_deployed


class FlowNode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name='flow_nodes')
    node_family = models.ForeignKey(NodeFamily, on_delete=models.CASCADE, related_name='flow_nodes')
    order = models.PositiveIntegerField()
    from_node = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='to_nodes')
    selected_subnode = models.ForeignKey(
        SubNode, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="SubNode selected for this FlowNode instance"
    )

    class Meta:
        unique_together = ('flow', 'node_family', 'order')
        ordering = ['order']

    def __str__(self):
        return f"FlowNode: {self.node_family.name} in {self.flow.name} at position {self.order}"

    def clean(self):
        if FlowNode.objects.filter(flow=self.flow, order=self.order).exclude(pk=self.pk).exists():
            raise ValidationError(f"Order {self.order} already exists in this flow.")
        if self.selected_subnode and getattr(self.selected_subnode, "node_family_id", None) != self.node_family_id:
            raise ValidationError("Selected subnode must belong to the same node family.")

    def save(self, *args, **kwargs):
        self.clean()
        creating = self._state.adding
        super().save(*args, **kwargs)

        if creating:
            # Automatically create edge from previous node if from_node not provided
            if not self.from_node:
                prev_node = FlowNode.objects.filter(flow=self.flow, order=self.order - 1).first()
                if prev_node:
                    Edge.objects.get_or_create(
                        flow=self.flow, from_node=prev_node, to_node=self
                    )
            else:
                Edge.objects.get_or_create(
                    flow=self.flow, from_node=self.from_node, to_node=self
                )


class FlowNodeParameter(models.Model):
    """
    Stores runtime parameter values for a FlowNode, typically copied from the selected subnode
    """
    flow_node = models.ForeignKey(FlowNode, on_delete=models.CASCADE, related_name='parameters')
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    value = models.JSONField()

    class Meta:
        unique_together = ('flow_node', 'parameter')

    def __str__(self):
        return f"{self.parameter.key} for {self.flow_node}"


class Edge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name='edges')
    from_node = models.ForeignKey(FlowNode, on_delete=models.CASCADE, related_name='out_edges')
    to_node = models.ForeignKey(FlowNode, on_delete=models.CASCADE, related_name='in_edges')
    condition = models.TextField(blank=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('flow', 'from_node', 'to_node')

    def __str__(self):
        return f"Edge from {self.from_node.node_family.name} to {self.to_node.node_family.name}"

    @staticmethod
    def check_edges_for_flownode(flownode):
        incoming = Edge.objects.filter(flow=flownode.flow, to_node=flownode).exists()
        outgoing = Edge.objects.filter(flow=flownode.flow, from_node=flownode).exists()
        if not incoming and not outgoing:
            raise ValidationError(
                f"Node '{flownode.node_family.name}' must have at least one incoming or outgoing edge in flow '{flownode.flow.name}'."
            )


class ExecutionLog(models.Model):
    STATUS_CHOICES = [
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name='flow_executions')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    context_data = models.JSONField(default=dict)
    error_message = models.TextField(blank=True, null=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Execution {self.id} of Flow {self.flow.name} - {self.status}"

    def save(self, *args, **kwargs):
        if self.status == 'running' and not self.flow.is_deployed:
            raise ValidationError("Only deployed flows can be set to running.")

        super().save(*args, **kwargs)

        # Maintain flow running state
        if self.status == 'running' and not self.flow.is_running:
            self.flow.is_running = True
            self.flow.save(update_fields=['is_running'])
        elif self.status in ('completed', 'failed'):
            if not self.flow.flow_executions.filter(status='running').exists():
                self.flow.is_running = False
                self.flow.save(update_fields=['is_running'])
