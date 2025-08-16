from rest_framework.routers import DefaultRouter
from .views import NodeFamilyViewSet, VersionViewSet

router = DefaultRouter()
router.register(r'node-families', NodeFamilyViewSet, basename='nodefamily')
router.register(r'node-families/(?P<family_id>[^/.]+)/versions', VersionViewSet, basename='nodeversion')

urlpatterns = router.urls