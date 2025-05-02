from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import DynamicModelViewSet, list_models, get_model_schema

router = DefaultRouter()
router.register(r'(?P<app_label>[^/]+)/(?P<model_name>[^/]+)', DynamicModelViewSet, basename='dynamic-model')

urlpatterns = [
    path('models/', list_models),
    path('schema/<str:app_label>/<str:model_name>/', get_model_schema),
]
urlpatterns += router.urls
