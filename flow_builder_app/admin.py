from django.contrib import admin

# Register your models here.
from flow_builder_app.node.models import *
from flow_builder_app.subnode.models import *
from flow_builder_app.parameter.models import *



admin.site.register(NodeFamily)
admin.site.register(NodeFamilyRelationship)
admin.site.register(NodeParameter)
admin.site.register(NodeVersion)
admin.site.register(NodeVersionLink)
admin.site.register(NodeExecution)