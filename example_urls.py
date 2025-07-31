"""
Example URL configuration for django-react-admin with embed functionality.

Add this to your main urls.py or create a separate urls file for the API.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter
from django_react_admin.views import DynamicModelViewSet, get_model_schema

# Create a router and register the dynamic viewset
router = DefaultRouter()

# URL patterns
urlpatterns = [
    # Dynamic model CRUD endpoints
    path('api/<str:app_label>/<str:model_name>/', 
         DynamicModelViewSet.as_view({
             'get': 'list',
             'post': 'create'
         }), 
         name='model-list'),
    
    path('api/<str:app_label>/<str:model_name>/<str:pk>/', 
         DynamicModelViewSet.as_view({
             'get': 'retrieve',
             'put': 'update',
             'patch': 'update',
             'delete': 'destroy'
         }), 
         name='model-detail'),
    
    # Additional endpoints for react-admin
    path('api/<str:app_label>/<str:model_name>/get_many/', 
         DynamicModelViewSet.as_view({'post': 'get_many', 'get': 'get_many'}), 
         name='model-get-many'),
    
    path('api/<str:app_label>/<str:model_name>/create_many/', 
         DynamicModelViewSet.as_view({'post': 'create_many'}), 
         name='model-create-many'),
    
    path('api/<str:app_label>/<str:model_name>/update_many/', 
         DynamicModelViewSet.as_view({'get': 'update_many'}), 
         name='model-update-many'),
    
    path('api/<str:app_label>/<str:model_name>/delete_many/', 
         DynamicModelViewSet.as_view({'get': 'delete_many'}), 
         name='model-delete-many'),
    
    path('api/<str:app_label>/<str:model_name>/export_data/', 
         DynamicModelViewSet.as_view({'get': 'export_data'}), 
         name='model-export'),
    
    path('api/<str:app_label>/<str:model_name>/import_data/', 
         DynamicModelViewSet.as_view({'post': 'import_data'}), 
         name='model-import'),
    
    # Model schema endpoint
    path('api/schema/<str:app_label>/<str:model_name>/', 
         get_model_schema, 
         name='model-schema'),
]

# Example usage with embed functionality:
"""
# Get a single post with embedded author:
GET /api/myapp/post/123/?meta={"embed": ["author"]}

# Get list of posts with embedded author and category:
GET /api/myapp/post/?meta={"embed": ["author", "category"]}

# Get multiple posts with embedded relations:
POST /api/myapp/post/get_many/?meta={"embed": ["author"]}
Body: {"ids": [123, 124, 125]}

# Or using GET:
GET /api/myapp/post/get_many/?filter={"id": [123, 124]}&meta={"embed": ["author"]}
"""
