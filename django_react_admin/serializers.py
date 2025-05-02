from rest_framework import serializers

def dynamic_serializer(model_class, nested_depth=1):
    """
    Generates a dynamic DRF serializer for any model.
    """
    class DynamicSerializer(serializers.ModelSerializer):
        class Meta:
            model = model_class
            fields = '__all__'
            depth = nested_depth  # How deep ForeignKeys are nested

    return DynamicSerializer
