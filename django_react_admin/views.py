import csv
import io
import json
import logging
from datetime import datetime
from uuid import UUID

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from os import path
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class AdminFullAccess(BasePermission):
    def has_permission(self, request, view):
        # # Allow GET for all authenticated users
        # if request.method in SAFE_METHODS:
        #     return request.user and request.user.is_authenticated
        # # Allow POST/PUT/DELETE only for Admins
        # return request.user and request.user.is_staff
        return True


class RoleBasedPermission(BasePermission):
    """
    Checks permissions based on Django's auth group permissions for the model
    associated with the view.
    """

    ACTION_PERMISSION_MAP = {
        "list": "view",
        "retrieve": "view",
        "get_many": "view",
        "create": "add",
        "update": "change",
        "update_many": "change",
        "destroy": "delete",
        "delete_many": "delete",
        "export_data": "view",
        "import_data": "add",
    }

    def _get_model_from_view(self, view):
        """Helper method to robustly get the model from the view."""
        if hasattr(view, "get_queryset"):
            return view.get_queryset().model
        if hasattr(view, "queryset"):
            return view.queryset.model
        if hasattr(view, "get_serializer"):
            return view.get_serializer().Meta.model

        # Fallback to kwargs-based model lookup
        app_label = view.kwargs.get("app_label")
        model_name = view.kwargs.get("model_name")
        if app_label and model_name:
            try:
                return apps.get_model(app_label, model_name)
            except LookupError:
                logger.warning(
                    f"Could not find model for app_label='{app_label}' and model_name='{model_name}'."
                )
                return None
        return None

    def has_permission(self, request, view):
        """
        Checks if the user has the required permission for the given action.
        """
        return True
        # if not request.user or not request.user.is_authenticated:
        #     return False

        # model = self._get_model_from_view(view)
        # if not model:
        #     # Deny access by default if model cannot be determined.
        #     # This is a secure default. If specific views without models
        #     # should be accessible, they need a different permission class.
        #     return False

        # action = getattr(view, "action", None)
        # permission_type = self.ACTION_PERMISSION_MAP.get(action)
        # if not permission_type:
        #     # If the action is not in our map, deny access by default.
        #     return False

        # app_label = model._meta.app_label
        # model_name = model._meta.model_name

        # permission_codename = f"{permission_type}_{model_name.lower()}"
        # permission = f"{app_label}.{permission_codename}"

        # return request.user.has_perm(permission)


class IsAdminOrReadOnly(IsAuthenticated):
    def has_permission(self, request, view):
        # is_authenticated = super().has_permission(request, view)
        # if request.method in ('GET', 'HEAD', 'OPTIONS'):
        #     return is_authenticated
        # return is_authenticated and request.user.is_staff
        return True


def model_to_dict(instance, exclude_password=True):
    data = {}
    for field in instance._meta.fields:
        # print("field:", field)
        value = getattr(instance, field.name)
        if exclude_password and field.name == "password":
            continue
        if field.is_relation:
            # If ForeignKey field, store the related object's ID only
            if value is not None:
                data[field.name] = value.pk
            else:
                data[field.name] = None
        else:
            data[field.name] = value
    return data


def update_relation(instance, data):
    for field in instance._meta.fields:
        if field.is_relation:
            # import pdb; pdb.set_trace()
            # If ForeignKey field, store the related object's ID only
            if (
                data.get(field.name)
                and field.foreign_related_fields[0].get_internal_type() == "UUIDField"
            ):
                # print("UUID value:", data.get(field.name))
                # import pdb; pdb.set_trace()
                if isinstance(data.get(field.name), str):
                    value = UUID(data.get(field.name))
                else:
                    value = UUID(data.get(field.name).id)
            else:
                value = data.get(field.name, None)
            if value is not None:
                data[field.name] = field.related_model.objects.get(pk=value)
            else:
                data[field.name] = None
    return data


def model_to_dict_nested(instance, exclude_password=True):
    data = {}
    for field in instance._meta.fields:
        value = getattr(instance, field.name)
        if exclude_password and field.name == "password":
            continue
        if field.is_relation:
            if value is not None:
                related_data = {}
                related_fields = value._meta.fields
                for rel_field in related_fields:
                    # Only simple fields like id and name
                    if rel_field.get_internal_type() in (
                        "CharField",
                        "IntegerField",
                        "AutoField",
                    ):
                        related_data[rel_field.name] = getattr(value, rel_field.name)
                data[field.name] = related_data
            else:
                data[field.name] = None
        else:
            data[field.name] = value
    return data


def get_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def parse_filters(filters, Model):
    q = Q()
    for key, value in filters.items():
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            # Convert string representation of a list to an actual list
            try:
                value = eval(value)
            except Exception:
                pass

        if isinstance(value, list):
            # If the value is a single-item list, extract the item
            value = value[0]

        # Check if the field is a UUIDField
        field = next(
            (f for f in Model._meta.fields if f.name == key.split("|")[0]), None
        )
        if field and field.get_internal_type() == "UUIDField":
            try:
                print("UUID value:", value)
                value = UUID(value)  # Validate and convert to UUID
            except (ValueError, TypeError):
                raise ValueError(f"'{value}' is not a valid UUID.")

        if "|op=" in key:
            field, op = key.split("|op=")
            if op in ("like", "ilike"):
                q &= Q(**{f"{field}__icontains": value})
            elif op == ">":
                q &= Q(**{f"{field}__gt": value})
            elif op == "<":
                q &= Q(**{f"{field}__lt": value})
        elif key == "q":
            search = Q()
            for field in Model._meta.fields:
                if field.get_internal_type() == "CharField":
                    search |= Q(**{f"{field.name}__icontains": value})
            q &= search
        else:
            q &= Q(**{key: value})
    return q


def get_foreign_key_field(child_model, parent_model):
    """
    Return the name of the ForeignKey field in child_model that points to parent_model.
    """
    for field in child_model._meta.fields:
        if field.is_relation and field.related_model == parent_model:
            return field.name
    return None


class DynamicModelViewSet(viewsets.ViewSet):
    # permission_classes = [IsAdminOrReadOnly]
    permission_classes = [RoleBasedPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    # app_label = "clothingapp"

    def get_model(self, app_label, model_name):
        model = get_model(app_label, model_name)
        print("model:", app_label, model_name)
        if not model:
            raise Exception("Invalid model")
        return model

    def list(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        filter_str = request.GET.dict().get("filter", "{}")
        try:
            filters = json.loads(filter_str)
        except Exception:
            filters = {}

        sort_param = request.GET.get("sort")
        if sort_param:
            try:
                sort = eval(sort_param)
                if not isinstance(sort, list) or len(sort) != 2:
                    raise ValueError("Sort parameter must be a list with two elements.")
            except Exception as e:
                return Response(
                    {"error": f"Invalid sort parameter: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            sort = [Model._meta.pk.name or Model._meta.fields[0].name, "ASC"]

        if not isinstance(sort, list) or len(sort) != 2:
            return Response(
                {"error": "Invalid sort parameter"}, status=status.HTTP_400_BAD_REQUEST
            )
        range_ = eval(request.GET.dict().get("range", "[0, 9]"))

        queryset = Model.objects.all()
        if filters:
            queryset = queryset.filter(parse_filters(filters, Model))
        if hasattr(Model, "is_deleted"):
            queryset = queryset.filter(is_deleted=False)
        if hasattr(Model, "unit_id") and request.headers.get("Unit-ID"):
            queryset = queryset.filter(unit_id=request.headers.get("Unit-ID"))

        if sort:
            field, order = sort
            if order == "DESC":
                field = f"-{field}"
            queryset = queryset.order_by(field)

        total_count = queryset.count()
        queryset = queryset[range_[0] : range_[1] + 1]
        response = Response([model_to_dict(obj) for obj in queryset])
        response["Content-Range"] = f"{range_[0]}-{range_[1]}/{total_count}"
        return response

    def retrieve(self, request, pk=None, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        try:
            obj = Model.objects.get(pk=pk)
            return Response(model_to_dict(obj))
        except Model.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    @transaction.atomic
    def create(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        data = dict(request.POST.dict()) if request.POST else dict(request.data)
        files = request.FILES.dict() if request.FILES else {}

        def recursive_create(model, data, parent_obj=None, parent_model=None):
            # Set created_by if exists
            if "created_by" in [f.name for f in model._meta.fields]:
                data["created_by"] = request.user.id

            # Set created_at if exists
            if "created_at" in [f.name for f in model._meta.fields]:
                data["created_at"] = datetime.utcnow()

            if hasattr(model, "unit_id") and request.headers.get("Unit-ID"):
                data["unit_id"] = request.headers.get("Unit-ID")

            # Update relation fields
            update_relation(model, data)

            # Detect child tables (list-type fields)
            # Only include children where the child model has FK to the parent model
            children = {}
            parent_data = {}
            for k, v in data.items():
                if isinstance(v, list):
                    try:
                        child_model = self.get_model(app_label, k)
                        fk_field = get_foreign_key_field(child_model, model)
                        if fk_field:
                            children[k] = v
                        else:
                            parent_data[k] = v
                    except Exception:
                        parent_data[k] = v
                        continue

                elif not isinstance(v, list):
                    parent_data[k] = v

            # Assign file fields if present
            for field in model._meta.fields:
                if (
                    field.get_internal_type() == "CharField"
                    and files
                    and field.name in files
                ):
                    data[field.name] = files[field.name]

            # If this is a child, set the foreign key to parent_obj
            if parent_obj and parent_model:
                fk_field = get_foreign_key_field(model, parent_model)
                if fk_field:
                    parent_data[fk_field] = parent_obj

            # Create parent
            try:
                obj = model(**parent_data)
                obj.full_clean()  # Validate model before creating
                obj.save()
                # obj = model.objects.create(**parent_data)
            except Exception as e:
                raise e

            # Handle children recursively
            for child_key, records in children.items():
                try:
                    child_model = self.get_model(app_label, child_key)
                except Exception:
                    continue
                # Only process if child_model has FK to current model
                fk_field = get_foreign_key_field(child_model, model)
                if not fk_field:
                    continue
                for record in records:
                    recursive_create(child_model, record, obj, model)
            return obj

        try:
            parent_obj = recursive_create(Model, data)
        except ValidationError as e:
            return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            # Example for unique constraint violation
            return Response(
                {"non_field_errors": [str(e)]}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(model_to_dict(parent_obj), status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, pk=None, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        data = dict(request.POST.dict()) if request.POST else dict(request.data)
        files = request.FILES.dict() if request.FILES else {}

        def save_file_and_get_url(file, folder="uploads"):
            filename = default_storage.save(path.join(folder, file.name), file)
            return default_storage.url(filename)

        def recursive_update(model, obj, data, parent_obj=None, parent_model=None):
            update_relation(model, data)
            # Only include children where the child model has FK to the parent model
            children = {}
            parent_data = {}
            for k, v in data.items():
                if isinstance(v, list):
                    try:
                        child_model = self.get_model(app_label, k)
                        fk_field = get_foreign_key_field(child_model, model)
                        if fk_field:
                            children[k] = v
                        else:
                            parent_data[k] = v
                    except Exception:
                        parent_data[k] = v
                        continue

                elif not isinstance(v, list):
                    parent_data[k] = v

            # Remove created_at if present
            if "created_at" in parent_data:
                del parent_data["created_at"]
            if "created_by" in parent_data:
                del parent_data["created_by"]
            # Update updated_at if exists
            if "updated_at" in [f.name for f in model._meta.fields]:
                parent_data["updated_at"] = datetime.utcnow()

            # If this is a child, set the foreign key to parent_obj
            if parent_obj and parent_model:
                fk_field = get_foreign_key_field(model, parent_model)
                if fk_field:
                    parent_data[fk_field] = parent_obj

            # Save files and set URL to CharField
            for field in model._meta.fields:
                if (
                    field.get_internal_type() == "CharField"
                    and files
                    and field.name in files
                ):
                    file_url = save_file_and_get_url(files[field.name])
                    setattr(obj, field.name, file_url)

            # Update parent object
            for k, v in parent_data.items():
                setattr(obj, k, v)

            try:
                obj.full_clean()  # Validate model before saving
                obj.save()
            except Exception as e:
                raise e

            # Handle children recursively
            for child_key, records in children.items():
                child_model = self.get_model(app_label, child_key)
                fk_field = get_foreign_key_field(child_model, model)
                if not fk_field:
                    continue

                existing_ids = []
                child_fields = [f.name for f in child_model._meta.fields]
                for item in records:
                    item[fk_field] = obj
                    if "updated_by" in child_fields:
                        item["updated_by"] = request.user.id

                    if "id" in item and item["id"]:
                        try:
                            child_obj = child_model.objects.get(id=item["id"])
                            recursive_update(child_model, child_obj, item, obj, model)
                            existing_ids.append(child_obj.id)
                        except child_model.DoesNotExist:
                            # If not found, create new
                            new_child = recursive_create(child_model, item, obj, model)
                            existing_ids.append(new_child.id)
                    else:
                        new_child = recursive_create(child_model, item, obj, model)
                        existing_ids.append(new_child.id)

                # Delete removed children
                child_model.objects.filter(**{fk_field: obj.id}).exclude(
                    id__in=existing_ids
                ).delete()

        def recursive_create(model, data, parent_obj=None, parent_model=None):
            # Set created_by if exists
            if "created_by" in [f.name for f in model._meta.fields]:
                data["created_by"] = request.user.id

            # Set created_at if exists
            if "created_at" in [f.name for f in model._meta.fields]:
                data["created_at"] = datetime.utcnow()

            update_relation(model, data)

            # Only include children where the child model has FK to the parent model
            children = {}
            parent_data = {}
            for k, v in data.items():
                if isinstance(v, list):
                    try:
                        child_model = self.get_model(app_label, k)
                        fk_field = get_foreign_key_field(child_model, model)
                        if fk_field:
                            children[k] = v
                        else:
                            parent_data[k] = v
                    except Exception:
                        parent_data[k] = v
                        continue

                elif not isinstance(v, list):
                    parent_data[k] = v

            if parent_obj and parent_model:
                fk_field = get_foreign_key_field(model, parent_model)
                if fk_field:
                    parent_data[fk_field] = parent_obj

            try:
                obj = model(**parent_data)
                obj.full_clean()  # Validate model before creating
                obj.save()  # Validate model before creating
                # obj = model.objects.create(**parent_data)
            except ValidationError as e:
                return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
            except IntegrityError as e:
                # Example for unique constraint violation
                return Response(
                    {"non_field_errors": [str(e)]}, status=status.HTTP_400_BAD_REQUEST
                )

            for child_key, records in children.items():
                try:
                    child_model = self.get_model(app_label, child_key)
                except Exception:
                    continue
                fk_field = get_foreign_key_field(child_model, model)
                if not fk_field:
                    continue
                for record in records:
                    recursive_create(child_model, record, obj, model)
            return obj

        try:
            obj = Model.objects.get(pk=pk)
            recursive_update(Model, obj, data)
            return Response(model_to_dict(obj))
        except ValidationError as e:
            return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            # Example for unique constraint violation
            return Response(
                {"non_field_errors": [str(e)]}, status=status.HTTP_400_BAD_REQUEST
            )
        except Model.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    def destroy(self, request, pk=None, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        try:
            obj = Model.objects.get(pk=pk)
            if hasattr(obj, "is_deleted"):
                obj.is_deleted = True
                obj.save()
            else:
                obj.delete()
            return Response(model_to_dict(obj))
        except Model.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def create_many(self, request, app_label=None, model_name=None):
        """
        Bulk create items for a model. Does not handle child table data.
        Expects a list of dicts in request.data["items"].
        """
        Model = self.get_model(app_label, model_name)
        items = request.data.get("items", [])
        if not isinstance(items, list) or not items:
            return Response(
                {"error": "No items provided"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Remove child table keys from each item
        model_fields = set(f.name for f in Model._meta.fields)
        cleaned_items = [
            {k: v for k, v in item.items() if k in model_fields} for item in items
        ]

        # Optionally, set created_by/created_at if present in model
        for item in cleaned_items:
            if "created_by" in model_fields and hasattr(request.user, "id"):
                item["created_by"] = request.user.id
            if "created_at" in model_fields:
                item["created_at"] = datetime.utcnow()
            if hasattr(Model, "unit_id") and request.headers.get("Unit-ID"):
                item["unit_id"] = request.headers.get("Unit-ID")
            update_relation(Model, item)

        objects = [Model(**item) for item in cleaned_items]

        try:
            # Model.objects.full_clean()  # Validate model before creating
            Model.objects.bulk_create(objects)
        except ValidationError as e:
            return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            # Example for unique constraint violation
            return Response(
                {"non_field_errors": [str(e)]}, status=status.HTTP_400_BAD_REQUEST
            )

        # Return created objects as list of dicts
        return Response(
            [model_to_dict(obj) for obj in objects], status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["post", "get"])
    def get_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        if request.method == "GET":
            filters = eval(request.GET.dict().get("filter", "{}"))
            ids = filters.get("id", [])
        else:
            ids = request.data.get("ids", [])

        queryset = Model.objects.filter(id__in=ids)
        if hasattr(Model, "is_deleted"):
            queryset = queryset.filter(is_deleted=False)
        data = [model_to_dict(obj) for obj in queryset]
        return Response(data)

    @action(detail=False, methods=["get"])
    def update_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        filters = eval(request.GET.dict().get("filter", "{}"))
        # ids = request.data.get("filter", {}).get("id", [])
        ids = filters.get("id", [])
        update_data = request.data.get("data", {})
        Model.objects.filter(id__in=ids).update(**update_data)
        return Response(ids)

    @action(detail=False, methods=["get"])
    def delete_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        filters = eval(request.GET.dict().get("filter", "{}"))
        # ids = request.data.get("filter", {}).get("id", [])
        ids = filters.get("id", [])
        if hasattr(Model, "is_deleted"):
            Model.objects.filter(id__in=ids).update(is_deleted=True)
        else:
            Model.objects.filter(id__in=ids).delete()
        return Response(ids)

    @action(detail=False, methods=["get"])
    def export_data(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        queryset = Model.objects.all()
        fields = [field.name for field in Model._meta.fields]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(fields)
        for obj in queryset:
            writer.writerow([getattr(obj, field) for field in fields])

        response = Response(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename={model_name}.csv"
        return response

    @action(detail=False, methods=["post"])
    def import_data(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        file = request.FILES.get("file")
        if not file:
            return Response({"error": "No file uploaded"}, status=400)

        decoded_file = file.read().decode("utf-8").splitlines()
        reader = csv.DictReader(decoded_file)
        objects = [Model(**row) for row in reader]
        Model.objects.bulk_create(objects)

        return Response({"message": "Imported successfully"})


@api_view(["GET"])
def get_model_schema(request, app_label, model_name):
    Model = get_model(app_label, model_name)
    if not Model:
        return Response({"error": "Invalid model"}, status=400)

    fields = []
    for field in Model._meta.fields:
        if field.name in [
            # "id",
            "unit_id",
            "created_at",
            "updated_at",
            "modified_at",
            "created_by",
            "updated_by",
            "modified_by",
        ]:
            continue
        field_type = field.get_internal_type()
        is_fk = field.is_relation and hasattr(field, "related_model")
        field_info = {
            "name": field.name,
            "type": field_type,
            "is_fk": is_fk,
            "related_model": None,
            "related_name": None,
            "is_required": field.blank is False and field.null is False,
            # "is_unique": field.unique,
            # "default": field.default if field.default is not None else None,
            # "verbose_name": field.verbose_name,
            # "help_text": field.help_text,
        }
        if is_fk:
            field_info["related_model"] = (
                f"{field.related_model._meta.app_label}/{field.related_model._meta.model_name}"
            )
            field_info["related_name"] = getattr(
                field.related_model._meta,
                "verbose_name_field",
                (
                    "name"
                    if "name"
                    in [f.name for f in field.related_model._meta.get_fields()]
                    else field.related_model._meta.fields[0].name
                ),
            )

        fields.append(field_info)

    return Response(
        {
            "app_label": app_label,
            "model_name": model_name,
            "fields": fields,
        }
    )
