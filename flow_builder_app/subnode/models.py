import uuid
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.db.models import Q, Max
from flow_builder_app.node.models import NodeFamily,NodeVersionLink,NodeVersion
from flow_builder_app.parameter.models import Parameter,ParameterValue

class SubNode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node_family = models.ForeignKey(NodeFamily, on_delete=models.CASCADE, related_name='subnodes')
    name = models.CharField(max_length=255)
    is_deployed = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    original = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='versions')
    description = models.TextField(blank=True, null=True)
    version_comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('node_family', 'name', 'version')
        ordering = ['node_family', 'name', 'version']

 

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)

        if is_new:
            # Get all versions of the parent family
            parent_versions = NodeVersion.objects.filter(family=self.node_family)

            # Get all versions of THIS subnode's family (same family)
            child_versions = NodeVersion.objects.filter(family=self.node_family)

            links_to_create = []
            for parent_version in parent_versions:
                for child_version in child_versions:
                    if not NodeVersionLink.objects.filter(
                        parent_version=parent_version,
                        child_version=child_version
                    ).exists():
                        links_to_create.append(
                            NodeVersionLink(
                                parent_version=parent_version,
                                child_version=child_version,
                                order=0
                            )
                        )

            if links_to_create:
                with transaction.atomic():
                    NodeVersionLink.objects.bulk_create(links_to_create)

    def clean(self):
        if self.is_deployed and not self.is_editable:
            raise ValidationError("Deployed versions must be read-only")
   
    def get_parameters_for_version(self, node_version):
        """
        Returns a dict of parameter key → value for this SubNode and NodeVersion.
        Uses default value if ParameterValue not set.
        """
        version_params = node_version.parameters.all()
        values = {}
        for param in version_params:
            pv = self.parameter_values.filter(parameter=param).first()
            values[param.key] = pv.value if pv else param.default_value
        return values
    def get_version_parameters(self, node_version):
        """
        Returns all parameters for a specific version with values
        Format: {param_key: {"value": x, "default": y, "source": "subnode|default"}}
        """
        result = {}
        version_params = node_version.parameters.all()
        
        for param in version_params:
            param_value = self.parameter_values.filter(parameter=param).first()
            
            if param_value:
                result[param.key] = {
                    "value": param_value.value,
                    "default": param.default_value,
                    "source": "subnode",
                    "datatype": param.datatype
                }
            else:
                result[param.key] = {
                    "value": param.default_value,
                    "default": param.default_value,
                    "source": "default",
                    "datatype": param.datatype
                }
                
        return result
    @property
    def is_editable(self):
        """Editable if not deployed"""
        return not self.is_deployed

    def create_new_version(self, version_comment=None):
        """Create a new version of this SubNode"""
        with transaction.atomic():
            max_version = self.get_all_versions().aggregate(Max('version'))['version__max'] or 0
            new_version = SubNode.objects.create(
                node_family=self.node_family,
                name=self.name,
                version=max_version + 1,
                original=self.original or self,
                description=self.description,
                version_comment=version_comment or f"New version created from v{self.version}",
                is_deployed=False
            )
            # Copy parameter values
            self._copy_parameter_values(new_version)
            return new_version

    def deploy(self):
        """Deploy this version and undeploy others"""
        with transaction.atomic():
            self.get_all_versions().update(is_deployed=False)
            self.is_deployed = True
            self.save()

    def undeploy(self):
        self.is_deployed = False
        self.save()

    def get_all_versions(self):
        """Get all versions of this SubNode"""
        if self.original:
            return SubNode.objects.filter(Q(original=self.original) | Q(pk=self.original.pk))
        return SubNode.objects.filter(Q(original=self) | Q(pk=self.pk))

    def get_active_version(self):
        """Get the currently deployed version"""
        return self.get_all_versions().filter(is_deployed=True).first()

    def get_last_version(self):
        return self.get_all_versions().order_by('-created_at').first()

    def _copy_parameter_values(self, new_version):
        """Copy parameter values to new version"""
        for param_value in self.parameter_values.all():
            param_value.pk = None
            param_value.subnode = new_version
            param_value.save()

    @classmethod
    def import_from_json(cls, data, node_family):
        """Import SubNode from JSON"""
        if cls.objects.filter(name=data['name'], node_family=node_family).exists():
            raise ValidationError("SubNode with this name already exists for this family")
        
        versions = data.get('versions', [])
        if not versions:
            raise ValidationError("No versions provided in import data")
        
        with transaction.atomic():
            # Create original version
            original = cls.objects.create(
                node_family=node_family,
                name=data['name'],
                version=1,
                description=data.get('description', ''),
                version_comment="Imported version",
                is_deployed=False
            )
            
            # Create other versions
            for version_data in versions[1:]:
                cls.objects.create(
                    node_family=node_family,
                    name=data['name'],
                    version=version_data['version'],
                    original=original,
                    description=version_data.get('description', ''),
                    version_comment=version_data.get('version_comment', ''),
                    is_deployed=False
                )
            
            return original



class SubNodeParameterValue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subnode = models.ForeignKey(SubNode, on_delete=models.CASCADE, related_name='parametervalues')
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    value = models.JSONField()

    class Meta:
        unique_together = ('subnode', 'parameter')

    def __str__(self):
        return f"{self.parameter.key} = {self.value} ({self.subnode.name})"