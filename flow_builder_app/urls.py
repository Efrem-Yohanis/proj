from django.urls import path, include, re_path
from rest_framework_nested import routers
from flow_builder_app.parameter.views import ParameterViewSet, ParameterValueViewSet
from flow_builder_app.subnode.views import SubNodeViewSet
from flow_builder_app.node.views import (
    NodeFamilyViewSet,
    VersionViewSet,
    DeploymentViewSet,
    VersionContentViewSet,
    ExecutionViewSet,
    NodeTestViewSet
)
from flow_builder_app.flow.views import (
    FlowViewSet,
    FlowNodeViewSet,
    FlowEdgeViewSet,
    FlowExecutionViewSet,
    NodeExecutionLogViewSet,
)

# Base router
router = routers.SimpleRouter()
router.register(r'parameters', ParameterViewSet, basename='parameter')
router.register(r'parameter-values', ParameterValueViewSet, basename='parametervalue')
router.register(r'subnodes', SubNodeViewSet, basename='subnode')
router.register(r'node-families', NodeFamilyViewSet, basename='nodefamily')
router.register(r'executions', ExecutionViewSet, basename='execution')  # <-- ADD THIS LINE

# --- Flow endpoints ---
router.register(r'flows', FlowViewSet, basename='flow')
router.register(r'flow-nodes', FlowNodeViewSet, basename='flownode')
router.register(r'flow-edges', FlowEdgeViewSet, basename='flowedge')
router.register(r'flow-executions', FlowExecutionViewSet, basename='flowexecution')
router.register(r'node-execution-logs', NodeExecutionLogViewSet, basename='nodeexecutionlog')

# Nested router for versions under node-families
version_router = routers.NestedSimpleRouter(router, r'node-families', lookup='family')
version_router.register(r'versions', VersionViewSet, basename='version')
version_router.register(r'executions', ExecutionViewSet, basename='family-execution')  # <-- Change basename

# Nested router for content under versions
content_router = routers.NestedSimpleRouter(version_router, r'versions', lookup='version')
content_router.register(r'content', VersionContentViewSet, basename='versioncontent')

# Node Test endpoint
router.register(r'nodes/test', NodeTestViewSet, basename='node-test')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(version_router.urls)),
    path('', include(content_router.urls)),
]

# Deployment URLs
deployment_urls = [
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/deploy/$',
        DeploymentViewSet.as_view({'post': 'deploy'}),
        name='version-deploy'
    ),
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/undeploy/$',
        DeploymentViewSet.as_view({'post': 'undeploy'}),
        name='version-undeploy'
    ),
]

# Script URLs
script_urls = [
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/script/$',
        VersionContentViewSet.as_view({'patch': 'script', 'get': 'get_script'}),
        name='version-script'
    ),
]

# Parameter URLs
param_urls = [
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/add_parameter/$',
        VersionContentViewSet.as_view({'post': 'add_parameter'}),
        name='version-add-parameter'
    ),
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/remove_parameter/$',
        VersionContentViewSet.as_view({'post': 'remove_parameter'}),
        name='version-remove-parameter'
    ),
]

# Execution action URLs - ADD THESE
execution_urls = [
    re_path(
        r'^executions/start/$',
        ExecutionViewSet.as_view({'post': 'start_execution'}),
        name='execution-start'
    ),
    re_path(
        r'^executions/(?P<pk>[^/]+)/stop/$',
        ExecutionViewSet.as_view({'post': 'stop_execution'}),
        name='execution-stop'
    ),
    re_path(
        r'^executions/(?P<pk>[^/]+)/status/$',
        ExecutionViewSet.as_view({'get': 'execution_status'}),
        name='execution-status'
    ),
    re_path(
        r'^executions/(?P<pk>[^/]+)/logs/$',
        ExecutionViewSet.as_view({'get': 'execution_logs'}),
        name='execution-logs'
    ),
]

urlpatterns += deployment_urls + param_urls + script_urls + execution_urls  # <-- Add execution_urls