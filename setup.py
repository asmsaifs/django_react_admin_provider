from setuptools import find_packages, setup

setup(
    name="django-react-admin",
    version="0.5.1",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "Django>=3.3",
        "djangorestframework",
    ],
    description="Dynamic Django DRF backend for React-Admin",
    author="ASM Saiful Islam Chowdhury",
    author_email="asmsaifs@yahoo.com",
    url="https://github.com/asmsaifs/django-react-admin",
    classifiers=[
        "Framework :: Django",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)