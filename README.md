# Django React-Admin Provider

Dynamic backend for React-Admin with DRF, RBAC, nested writes, model/schema introspection, and embed functionality.

## Features

- **Dynamic Model Access**: Automatically generate CRUD endpoints for any Django model
- **Role-Based Access Control (RBAC)**: Built-in permission system
- **Nested Writes**: Support for creating and updating related models
- **Model Introspection**: Automatic schema generation
- **Embed Functionality**: Include related objects in API responses (react-admin compatible)

## Embed Functionality

The embed functionality allows you to include related objects in your API responses, following the react-admin documentation pattern. This is particularly useful for reducing the number of API calls needed to display related data.

### Usage

#### Basic Embed Example

```http
GET /api/myapp/post/123/?meta={"embed": ["author"]}
```

Response:
```json
{
    "id": 123,
    "title": "Hello, world",
    "author_id": 456,
    "author": {
        "id": 456,
        "name": "John Doe",
        "email": "john@example.com"
    }
}
```

#### Multiple Embeds

```http
GET /api/myapp/post/?meta={"embed": ["author", "category"]}
```

Response:
```json
[
    {
        "id": 123,
        "title": "Hello, world",
        "author_id": 456,
        "category_id": 789,
        "author": {
            "id": 456,
            "name": "John Doe",
            "email": "john@example.com"
        },
        "category": {
            "id": 789,
            "name": "Technology",
            "description": "Tech related posts"
        }
    }
]
```

### React Admin Integration

```javascript
// In your React Admin data provider:
const { data } = useGetOne('posts', { 
    id: 123, 
    meta: { embed: ['author'] } 
});

// Or for lists:
const { data } = useGetList('posts', {
    pagination: { page: 1, perPage: 10 },
    sort: { field: 'id', order: 'ASC' },
    filter: {},
    meta: { embed: ['author', 'category'] }
});
```

### Supported Endpoints

The embed functionality is supported on:
- `GET /api/{app_label}/{model_name}/` (list)
- `GET /api/{app_label}/{model_name}/{id}/` (retrieve)
- `POST /api/{app_label}/{model_name}/get_many/` (get multiple)

### Performance Optimization

The implementation automatically uses Django's `select_related()` to optimize database queries when embed parameters are provided, preventing N+1 query problems.

### Example Model Structure

```python
from django.db import models

class Author(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    
class Category(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField()

class Post(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```

## Installation

```bash
pip install django-react-admin
```

## Configuration

Add to your Django settings:

```python
INSTALLED_APPS = [
    # ... other apps
    'django_react_admin',
]
```