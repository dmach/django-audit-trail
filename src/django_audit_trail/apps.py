from django.apps import AppConfig, apps
from django.core.checks import register, Tags
from django.db.models.fields.related_descriptors import ManyToManyDescriptor


class DjangoAuditTrailConfig(AppConfig):
    name = "django_audit_trail"

    def ready(self):
        # 1. Register system checks
        from .checks import check_audit_trail_models
        register(Tags.models)(check_audit_trail_models)

        # 2. Configure custom time-travel and soft-delete aware Many-to-Many descriptors.
        # We must replace descriptors here in ready() instead of the AuditTrailMeta metaclass.
        # At metaclass execution time, relationships are often unresolved strings to prevent
        # circular imports, and reverse relations on target models aren't fully wired up yet.
        self.configure_m2m_descriptors()

    def configure_m2m_descriptors(self):
        """
        Explicitly replaces Django's default ManyToManyDescriptor with our custom
        time-travel aware descriptor, but ONLY for relationships that use an
        explicit AuditTrailModel as their 'through' table.
        """
        from .models import AuditTrailModel, AuditTrailManyToManyDescriptor

        for model in apps.get_models():
            # Scan all M2M fields on the model
            for field in model._meta.many_to_many:
                through = getattr(field.remote_field, "through", None)

                # If the through model is an audited model, we explicitly upgrade the descriptors!
                if through and issubclass(through, AuditTrailModel):
                    # 1. Replace the forward descriptor on the source model
                    descriptor = getattr(model, field.name, None)
                    if isinstance(descriptor, ManyToManyDescriptor) and not isinstance(descriptor, AuditTrailManyToManyDescriptor):
                        setattr(model, field.name, AuditTrailManyToManyDescriptor(descriptor.rel, reverse=descriptor.reverse))

                    # 2. Replace the reverse descriptor on the target model
                    target_model = field.remote_field.model
                    reverse_attr = field.remote_field.get_accessor_name()
                    if reverse_attr:
                        rev_descriptor = getattr(target_model, reverse_attr, None)
                        if isinstance(rev_descriptor, ManyToManyDescriptor) and not isinstance(rev_descriptor, AuditTrailManyToManyDescriptor):
                            setattr(target_model, reverse_attr, AuditTrailManyToManyDescriptor(rev_descriptor.rel, reverse=rev_descriptor.reverse))
