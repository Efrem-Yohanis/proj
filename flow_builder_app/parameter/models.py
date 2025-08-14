import uuid
import json
import os
from django.db import models
from django.core.exceptions import ValidationError

from flow_builder_app.subnode.models import SubNode 

class Parameter(models.Model):
    DATA_TYPE_CHOICES = [
        ('string', 'String'),
        ('integer', 'Integer'),
        ('float', 'Float'),
        ('boolean', 'Boolean'),
        ('date', 'Date'),
        ('datetime', 'DateTime'),
        ('json', 'JSON'),
        ('file', 'File'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=100, unique=True)
    default_value = models.TextField(blank=True)
    datatype = models.CharField(max_length=100, choices=DATA_TYPE_CHOICES, blank=True, null=True)
    is_active = models.BooleanField(default=False)  # True = deployed, False = draft

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.key

    def clean(self):
        if self.default_value:
            try:
                if self.datatype == 'integer':
                    int(self.default_value)
                elif self.datatype == 'float':
                    float(self.default_value)
                elif self.datatype == 'boolean':
                    if self.default_value not in ['True', 'False', 'true', 'false', '1', '0']:
                        raise ValueError()
                elif self.datatype == 'json':
                    json.loads(self.default_value)
            except Exception:
                raise ValidationError(
                    f"Default value '{self.default_value}' does not match datatype '{self.datatype}'."
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

        # Lazy import to avoid circular import
        from flow_builder_app.subnode.models import SubNode

        # Update each related subnode config
        for subnode in getattr(self, 'subnodes', []):  # Optional: if you keep a relation
            node_dir = getattr(subnode, 'get_directory_path', lambda: None)()
            if node_dir:
                config_path = os.path.join(node_dir, "config.json")
                os.makedirs(node_dir, exist_ok=True)
                if os.path.exists(config_path):
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                else:
                    config = {}
                config[self.key] = self.default_value
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=4)

class ParameterValue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    subnode = models.ForeignKey(SubNode, on_delete=models.CASCADE, related_name='parameter_values')
    value = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ('parameter', 'subnode')

    def __str__(self):
        return f"{self.parameter.key}: {self.value} (SubNode: {getattr(self.subnode, 'name', '')})"

    def clean(self):
        if self.value:
            try:
                if self.parameter.datatype == 'integer':
                    int(self.value)
                elif self.parameter.datatype == 'float':
                    float(self.value)
                elif self.parameter.datatype == 'boolean':
                    if self.value not in ['True', 'False', 'true', 'false', '1', '0']:
                        raise ValueError()
                elif self.parameter.datatype == 'json':
                    json.loads(self.value)
            except Exception:
                raise ValidationError(
                    f"Value '{self.value}' does not match datatype '{self.parameter.datatype}'."
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
