from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from flow_builder_app.node.models import NodeVersion, NodeVersionLink
from flow_builder_app.subnode.models import SubNode
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from flow_builder_app.node.models import NodeVersion, NodeVersionLink

@receiver(post_save, sender=NodeVersion)
def link_existing_subnodes(sender, instance, created, **kwargs):
    if not created:
        return  # Only run on new versions

    # Get all subnodes in this family
    subnodes = instance.family.subnodes.all()

    links_to_create = []
    with transaction.atomic():
        for subnode in subnodes:
            # Get latest published (or all) versions of the subnode's family
            sub_versions = NodeVersion.objects.filter(family=subnode.node_family)

            for sub_version in sub_versions:
                if not NodeVersionLink.objects.filter(
                    parent_version=instance,
                    child_version=sub_version
                ).exists():
                    links_to_create.append(
                        NodeVersionLink(
                            parent_version=instance,
                            child_version=sub_version,
                            order=0
                        )
                    )

        if links_to_create:
            NodeVersionLink.objects.bulk_create(links_to_create)
