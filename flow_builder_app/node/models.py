from django.db import models
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db.models import Max

class NodeFamily(models.Model):
    """Represents a group of versioned nodes"""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=255)
    is_deployed = models.BooleanField(default=False)

    child_nodes = models.ManyToManyField(
        'self',
        through='NodeFamilyRelationship',
        symmetrical=False,
        related_name='parent_nodes'
    )

    class Meta:
        verbose_name_plural = "Node Families"
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_deployed']),
        ]

    def clean(self):
        if self.pk:
            original = NodeFamily.objects.get(pk=self.pk)
            if original.name != self.name and original.versions.exists():
                raise ValidationError("Cannot rename a family with existing versions")

    def has_deployed_versions(self):
        return self.versions.filter(state='published').exists()

    def __str__(self):
        return f"{self.name} (Family)"


class NodeFamilyRelationship(models.Model):
    """Through model for subnode relationships"""
    parent = models.ForeignKey(
        NodeFamily,
        on_delete=models.CASCADE,
        related_name='child_relationships'
    )
    child = models.ForeignKey(
        NodeFamily,
        on_delete=models.CASCADE,
        related_name='parent_relationships'
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('parent', 'child')
        ordering = ['order']


class NodeVersion(models.Model):
    VERSION_STATES = (
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived')
    )

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    family = models.ForeignKey(
        NodeFamily, on_delete=models.CASCADE, related_name='versions'
    )
    version = models.PositiveIntegerField(blank=True, null=True)
    state = models.CharField(max_length=20, choices=VERSION_STATES, default='draft')
    script = models.FileField(upload_to='node_scripts/%Y/%m/%d/', null=True, blank=True)
    changelog = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=255)

    linked_subversions = models.ManyToManyField(
        'self',
        through='NodeVersionLink',
        symmetrical=False,
        related_name='parent_versions'
    )

    class Meta:
        unique_together = [('family', 'version')]
        ordering = ['family', '-version']
        indexes = [
            models.Index(fields=['family', 'state']),
            models.Index(fields=['state']),
        ]

    def clean(self):
        if self.state == 'published' and not self.script:
            raise ValidationError("Published versions must have a script")

    def save(self, *args, **kwargs):
        if self.version is None:
            last_version = NodeVersion.objects.filter(family=self.family).aggregate(
                Max('version')
            )['version__max'] or 0
            self.version = last_version + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.family.name} v{self.version} ({self.state})"


class NodeParameter(models.Model):
    """Through model for version-specific parameter values"""
    node_version = models.ForeignKey(
        NodeVersion,
        on_delete=models.CASCADE,
        related_name='parameters'
    )
    parameter = models.ForeignKey(
        'flow_builder_app.Parameter',
        on_delete=models.CASCADE,
        related_name='node_parameters'
    )
    value = models.JSONField()

    class Meta:
        unique_together = [('node_version', 'parameter')]
        verbose_name = "Version Parameter"

    def __str__(self):
        return f"{self.parameter.key} on {self.node_version}"


class NodeVersionLink(models.Model):
    """Through model for version-specific subnode links"""
    parent_version = models.ForeignKey(
        NodeVersion,
        on_delete=models.CASCADE,
        related_name='child_links'
    )
    child_version = models.ForeignKey(
        NodeVersion,
        on_delete=models.CASCADE,
        related_name='parent_links'
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('parent_version', 'child_version')
        ordering = ['order']

    def clean(self):
        if self.parent_version.family == self.child_version.family:
            raise ValidationError("Cannot link versions from the same family")

        if not NodeFamilyRelationship.objects.filter(
            parent=self.parent_version.family,
            child=self.child_version.family
        ).exists():
            raise ValidationError("Child version must be from a subnode family")

        if self.child_version.state != 'published':
            raise ValidationError("Can only link to published versions")


class NodeExecution(models.Model):
    """Execution record for a node version"""
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('stopped', 'Stopped')
    ]

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    version = models.ForeignKey(
        NodeVersion,
        on_delete=models.PROTECT,
        related_name='executions'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    log = models.TextField(blank=True)
    triggered_by = models.CharField(max_length=255)
    artifacts = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['version', 'status']),
            models.Index(fields=['status']),
        ]

    @property
    def duration(self):
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def clean(self):
        if self.status in ['completed', 'failed', 'stopped'] and not self.completed_at:
            raise ValidationError("Completed executions must have an end time")
        if self.status == 'running' and not self.started_at:
            raise ValidationError("Running executions must have a start time")
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValidationError("Completed time cannot be before started time")

    def __str__(self):
        return f"Execution {self.id.hex[:8]} of {self.version} ({self.status})"