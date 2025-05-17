import csv
import io
from uuid import UUID
from datetime import datetime

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import api_view


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
            # If ForeignKey field, store the related object's ID only
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

        if isinstance(value, list) and len(value) == 1:
            # If the value is a single-item list, extract the item
            value = value[0]

        # Check if the field is a UUIDField
        field = next((f for f in Model._meta.fields if f.name == key.split("|")[0]), None)
        if field and field.get_internal_type() == "UUIDField":
            try:
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
            return Response({"error": "Invalid sort parameter"}, status=status.HTTP_400_BAD_REQUEST)
        range_ = eval(request.GET.dict().get("range", "[0, 9]"))

        queryset = Model.objects.all()
        if filters:
            queryset = queryset.filter(parse_filters(filters, Model))
        if hasattr(Model, "is_deleted"):
            queryset = queryset.filter(is_deleted=False)

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
        data = dict(request.data)

        if "created_by" in [f.name for f in Model._meta.fields]:
            data["created_by"] = request.user.id

        data["created_at"] = datetime.utcnow()
        update_relation(Model, data)

        # Detect child tables (list-type fields)
        children = {k: v for k, v in data.items() if isinstance(v, list)}
        parent_data = {k: v for k, v in data.items() if not isinstance(v, list)}
        # Create parent
        if "created_at" in [f.name for f in Model._meta.fields]:
            parent_data["created_at"] = datetime.utcnow()

        parent_obj = Model.objects.create(**parent_data)

        # Handle children
        for child_key, records in children.items():
            # infer child model
            try:
                child_model = get_model(app_label, child_key)
            except Exception:
                continue

            fk_field = get_foreign_key_field(child_model, Model)
            if not fk_field:
                continue

            # Set created_by for children if field exists
            child_fields = [f.name for f in child_model._meta.fields]
            for record in records:
                if "created_by" in child_fields:
                    record["created_by"] = request.user.id

            objs = [
                child_model(**{**record, fk_field: parent_obj}) for record in records
            ]
            child_model.objects.bulk_create(objs)

        return Response(model_to_dict(parent_obj), status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, pk=None, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        data = dict(request.data)

        update_relation(Model, data)

        children = {k: v for k, v in data.items() if isinstance(v, list)}
        parent_data = {k: v for k, v in data.items() if not isinstance(v, list)}

        try:
            obj = Model.objects.get(pk=pk)

            if "created_at" in parent_data:
                del parent_data["created_at"]
            if "updated_at" in [f.name for f in Model._meta.fields]:
                parent_data["updated_at"] = datetime.utcnow()

            for k, v in parent_data.items():
                setattr(obj, k, v)
            obj.save()

            # Handle children
            for child_key, records in children.items():
                child_model = get_model(app_label, child_key)
                # fk_field = f"{model_name.lower()}_id"
                fk_field = get_foreign_key_field(child_model, Model)
                if not fk_field:
                    continue

                existing_ids = []
                for item in records:
                    item[fk_field] = obj
                    if "id" in item:
                        existing_ids.append(item["id"])
                        child_model.objects.filter(id=item["id"]).update(**item)
                    else:
                        existing_ids.append(child_model.objects.create(**item).id)

                print("existing_ids ->", existing_ids)
                # Delete removed children
                child_model.objects.filter(**{fk_field: obj.id}).exclude(
                    id__in=existing_ids
                ).delete()

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
            return Response({"data": model_to_dict(obj)})
        except Model.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["post"])
    def get_many(self, request, model_name=None):
        Model = self.get_model(self.app_label, model_name)
        ids = request.data.get("filter", {}).get("id", [])
        queryset = Model.objects.filter(id__in=ids)
        if hasattr(Model, "is_deleted"):
            queryset = queryset.filter(is_deleted=False)
        data = [model_to_dict(obj) for obj in queryset]
        return Response({"data": data})

    @action(detail=False, methods=["post"])
    def update_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        ids = request.data.get("filter", {}).get("id", [])
        update_data = request.data.get("data", {})
        Model.objects.filter(id__in=ids).update(**update_data)
        return Response({"data": ids})

    @action(detail=False, methods=["post"])
    def delete_many(self, request, app_label=None, model_name=None):
        Model = self.get_model(app_label, model_name)
        ids = request.data.get("filter", {}).get("id", [])
        if hasattr(Model, "is_deleted"):
            Model.objects.filter(id__in=ids).update(is_deleted=True)
        else:
            Model.objects.filter(id__in=ids).delete()
        return Response({"data": ids})

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

    return Response({
        "app_label": app_label,
        "model_name": model_name,
        "fields": fields,
    })
