# # flow_builder_app/routing.py
# from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack
# from node.routing import websocket_urlpatterns as node_ws

# application = ProtocolTypeRouter({
#     "websocket": AuthMiddlewareStack(
#         URLRouter(
#             node_ws
#         )
#     ),
# })


from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/executions/(?P<execution_id>[^/]+)/logs/$", consumers.LogConsumer.as_asgi()),
    re_path(r"ws/logs/$", consumers.LogConsumer.as_asgi()),
]