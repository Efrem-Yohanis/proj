from django.apps import AppConfig
import asyncio

class FlowBuilderAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'flow_builder_app'
     
     
    def ready(self):
        import flow_builder_app.subnode.signals  # ensure signal is imported


class NodeConfig(AppConfig):
    name = 'flow_builder_app.node'
    verbose_name = 'Node Management'

    def ready(self):
        from .websocket_logger import broadcaster
        
        # Start the broadcaster message processing
        async def start_broadcaster():
            await broadcaster.start()
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(start_broadcaster())
            else:
                loop.run_until_complete(start_broadcaster())
        except RuntimeError:
            # Event loop not available (e.g., during migrations)
            pass