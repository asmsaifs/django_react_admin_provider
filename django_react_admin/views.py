import csv
import io
from datetime import datetime
from uuid import UUID

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from os import path
from django.core.files.storage import default_storage


class AdminFullAccess(BasePermission):
    def has_permission(self, request, view):
        # # Allow GET for all authenticated users
        # if request.method in SAFE_METHODS:
        #     return request.user and request.user.is_authenticated
        # # Allow POST/PUT/DELETE only for Admins
        # return request.user and request.user.is_staff
        return True


class RoleBasedPermission(BasePermission):
    def has_permission(self, request, view):
        # import pdb; pdb.set_trace()
        # if view.action in ['list', 'retrieve', 'get_many']:
        #     return request.user.is_authenticated  # All logged in users
        # elif view.action in ['create', 'update', 'update_many']:
        #     return request.user.groups.filter(name__in=['Staff', 'admin']).exists()
        # elif view.action in ['destroy', 'delete_many']:
        #     return request.user.is_superuser  # Only superadmins
        # return False
        return True


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
            # else:
            #     data[field.name] = None
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
                # print("UUID value:", value)
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
        # print("model:", app_label, model_name)
        if not model:
            raise Exception("Invalid model")
        return model

    def save_file_and_get_url(self, file, folder="uploads"):
        filename = default_storage.save(path.join(folder, file.name), file)
        return default_storage.url(filename)

    def list(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        filters = eval(request.GET.dict().get("filter", "{}"))

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

        paginator = PageNumberPagination()
        paginator.page_size = range_[1] - range_[0] + 1
        page = paginator.paginate_queryset(queryset, request)
        response = Response([model_to_dict(obj) for obj in page])
        response["Content-Range"] = f"{range_[0]}-{range_[1]}/{queryset.count()}"
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
            obj = model.objects.create(**parent_data)

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

        parent_obj = recursive_create(Model, data)
        return Response(model_to_dict(parent_obj), status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, pk=None, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        data = dict(request.POST.dict()) if request.POST else dict(request.data)
        # print("data to update:", data)
        files = request.FILES.dict() if request.FILES else {}

        def recursive_update(model, obj, data, parent_obj=None, parent_model=None):
            # print("Updating model1:", model, "with data1:", data)
            update_relation(model, data)
            # print("Updating model2:", model, "with data2:", data)
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

            # Update parent object
            for k, v in parent_data.items():
                # print("Updating field:", k, "with value:", v)
                if files and k in files:
                    file_url = self.save_file_and_get_url(files[k])
                    # print("Uploaded file URL:", file_url)
                    setattr(obj, k, file_url)
                else:
                    setattr(obj, k, v)

            # print("Saving parent object:", obj.__dict__)
            obj.save()

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

            obj = model.objects.create(**parent_data)

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

    @action(detail=False, methods=["get"])
    def get_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        filters = eval(request.GET.dict().get("filter", "{}"))
        # ids = request.data.get("filter", {}).get("id", [])
        ids = filters.get("id", [])
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
