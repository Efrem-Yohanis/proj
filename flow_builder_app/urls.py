from django.urls import path, include, re_path
from rest_framework_nested import routers
from flow_builder_app.parameter.views import ParameterViewSet, ParameterValueViewSet
from flow_builder_app.subnode.views import SubNodeViewSet
from flow_builder_app.node.views import (
    NodeFamilyViewSet,
    VersionViewSet,
    DeploymentViewSet,
    VersionContentViewSet,
    ExecutionViewSet
)

# Base router
router = routers.SimpleRouter()
router.register(r'parameters', ParameterViewSet, basename='parameter')
router.register(r'parameter-values', ParameterValueViewSet, basename='parametervalue')
router.register(r'subnodes', SubNodeViewSet, basename='subnode')
router.register(r'node-families', NodeFamilyViewSet, basename='nodefamily')

# Nested router for versions under node-families
version_router = routers.NestedSimpleRouter(router, r'node-families', lookup='family')
version_router.register(r'versions', VersionViewSet, basename='version')
version_router.register(r'executions', ExecutionViewSet, basename='execution')

# Nested router for content under versions
content_router = routers.NestedSimpleRouter(version_router, r'versions', lookup='version')
content_router.register(r'content', VersionContentViewSet, basename='versioncontent')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(version_router.urls)),
    path('', include(content_router.urls)),
]

# Deployment URLs (added separately for cleaner endpoints)
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

script_urls = [
    re_path(
        r'^node-families/(?P<family_id>[^/]+)/versions/(?P<version>[^/]+)/script/$',
        VersionContentViewSet.as_view({'patch': 'script', 'get': 'get_script'}),
        name='version-script'
    ),
]
# Parameter URLs (with fixed typo)
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

urlpatterns += deployment_urls + param_urls + script_urls