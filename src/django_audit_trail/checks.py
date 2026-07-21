from django.core.checks import Error
from django.apps import apps


def check_audit_trail_models(app_configs, **kwargs):
    """
    Django system check that enforces clean and high-integrity relationships:
    1. Disallows ManyToManyFields inside the nested State class of an AuditTrailModel.
    2. Mandates that ManyToManyFields on the Anchor model use an explicit through model
       that inherits from AuditTrailModel.
    """
    from .models import AuditTrailModel
    errors = []

    for model in apps.get_models():
        if not issubclass(model, AuditTrailModel) or model is AuditTrailModel:
            continue

        # 1. Check if ManyToManyField was defined on the companion State model
        state_model = getattr(model, "_state_model", None)
        if state_model:
            for field in state_model._meta.local_many_to_many:
                errors.append(
                    Error(
                        f"ManyToManyField '{field.name}' is defined on the State companion of model '{model.__name__}'.",
                        hint="ManyToManyFields are disallowed inside the nested 'State' class because they cause "
                             "excessive data duplication (snapshotting). Define the ManyToManyField directly "
                             "on the anchor model instead.",
                        obj=field,
                        id="django_audit_trail.E001",
                    )
                )

        # 2. Check ManyToManyFields on the Anchor model
        for field in model._meta.local_many_to_many:
            through_model = field.remote_field.through
            # If through model is auto-generated (not subclass of AuditTrailModel)
            if not issubclass(through_model, AuditTrailModel):
                errors.append(
                    Error(
                        f"ManyToManyField '{field.name}' on audited model '{model.__name__}' "
                        f"must use an explicit 'through' model that inherits from 'AuditTrailModel'.",
                        hint="Define an explicit intermediate model inheriting from AuditTrailModel and set through=YourThroughModel.",
                        obj=field,
                        id="django_audit_trail.E002",
                    )
                )

    return errors
