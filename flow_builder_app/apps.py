from django.apps import AppConfig


class FlowBuilderAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'flow_builder_app'
     
     
    def ready(self):
        import flow_builder_app.subnode.signals  # ensure signal is imported